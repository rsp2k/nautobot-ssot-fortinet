"""Nautobot-side adapter — scoping + ORM-shape translation tests.

Unit-test scope is intentionally narrow: the end-to-end firewall sync
(``development/scripts/e2e_firewall_sync.py``) hits real ORM behavior and
is the authoritative integration test. These unit tests only cover the
two pieces that wouldn't be caught by additive-only e2e:

1. The hostname/vdom **name_prefix** is built correctly and used as the
   ``filter(name__startswith=...)`` argument — if this is wrong, the
   adapter would silently load the wrong records (or none).
2. ``_orm_address_value()`` returns the right (type, value) tuple for
   each of the 4 FK shapes — the inverse of the FortiGate adapter's
   ``_address_value()``.
"""

from unittest.mock import MagicMock, patch

from nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall import (
    NautobotFirewallAdapter,
    _orm_address_value,
    _strip_original_name_prefix,
    _strip_prefix,
)


class TestNamePrefixConstruction:
    def test_default_vdom_root(self):
        a = NautobotFirewallAdapter(hostname="fgt-edge1")
        assert a.name_prefix == "fgt-edge1__root__"

    def test_explicit_vdom(self):
        a = NautobotFirewallAdapter(hostname="fgt-edge1", vdom="dmz")
        assert a.name_prefix == "fgt-edge1__dmz__"

    def test_two_adapters_disjoint_prefixes(self):
        a = NautobotFirewallAdapter(hostname="fgt-1", vdom="root")
        b = NautobotFirewallAdapter(hostname="fgt-2", vdom="root")
        assert a.name_prefix != b.name_prefix


class TestLoadAddressesScoping:
    """Verify _load_addresses calls filter() with the right prefix."""

    @patch("nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall.NautobotFirewallAdapter._load_address_groups")
    @patch("nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall.NautobotFirewallAdapter._load_services")
    @patch("nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall.NautobotFirewallAdapter._load_service_groups")
    def test_filter_called_with_hostname_vdom_prefix(self, _sg, _s, _ag):
        # Patch the ORM model import inside load() — load() does
        # `from nautobot_firewall_models.models import AddressObject` so we
        # have to stub the module entry.
        import sys
        from unittest.mock import MagicMock

        fake_mod = MagicMock()
        fake_addr_cls = MagicMock()
        fake_addr_cls.objects.filter.return_value = []
        fake_mod.AddressObject = fake_addr_cls
        # The other models referenced by load()'s import line are also stubbed
        # via the load() body — we patched the load helpers above so they
        # never execute, so we only care about AddressObject here.
        fake_mod.AddressObjectGroup = MagicMock()
        fake_mod.ServiceObject = MagicMock()
        fake_mod.ServiceObjectGroup = MagicMock()

        with patch.dict(sys.modules, {"nautobot_firewall_models.models": fake_mod}):
            a = NautobotFirewallAdapter(hostname="fgt-test", vdom="lab")
            a.load()

        fake_addr_cls.objects.filter.assert_called_once_with(name__startswith="fgt-test__lab__")


class TestOrmAddressValue:
    """_orm_address_value picks the right FK branch."""

    def test_prefix_branch(self):
        obj = MagicMock(prefix_id=1, fqdn_id=None, ip_range_id=None, ip_address_id=None)
        obj.prefix.prefix = "10.0.10.0/24"
        assert _orm_address_value(obj) == ("ipmask", "10.0.10.0/24")

    def test_fqdn_branch(self):
        obj = MagicMock(prefix_id=None, fqdn_id=7, ip_range_id=None, ip_address_id=None)
        obj.fqdn.name = "salesforce.com"
        assert _orm_address_value(obj) == ("fqdn", "salesforce.com")

    def test_iprange_branch(self):
        obj = MagicMock(prefix_id=None, fqdn_id=None, ip_range_id=3, ip_address_id=None)
        obj.ip_range.start_address = "10.99.0.10"
        obj.ip_range.end_address = "10.99.0.250"
        assert _orm_address_value(obj) == ("iprange", "10.99.0.10-10.99.0.250")

    def test_ipaddress_branch(self):
        obj = MagicMock(prefix_id=None, fqdn_id=None, ip_range_id=None, ip_address_id=42)
        obj.ip_address.host = "192.168.1.5"
        assert _orm_address_value(obj) == ("ipaddress", "192.168.1.5")

    def test_all_null_returns_none(self):
        obj = MagicMock(prefix_id=None, fqdn_id=None, ip_range_id=None, ip_address_id=None)
        assert _orm_address_value(obj) == (None, "")


class TestStripPrefix:
    def test_removes_matching_prefix(self):
        assert _strip_prefix("fgt-1__root__WEB_SERVERS", "fgt-1__root__") == "WEB_SERVERS"

    def test_non_matching_returns_input(self):
        assert _strip_prefix("UNRELATED", "fgt-1__root__") == "UNRELATED"

    def test_empty_remainder(self):
        assert _strip_prefix("fgt-1__root__", "fgt-1__root__") == ""


class TestStripOriginalNamePrefix:
    def test_strips_original_name_and_separator(self):
        # Convention: description is stored as "<original>: <description>"
        assert _strip_original_name_prefix("WEB_SERVERS: Public web tier", "WEB_SERVERS") == "Public web tier"

    def test_description_equals_original_name_only(self):
        # When original_name was used as fallback description, recover empty.
        assert _strip_original_name_prefix("WEB_SERVERS", "WEB_SERVERS") == ""

    def test_unrelated_description_passes_through(self):
        assert _strip_original_name_prefix("Hand edit by ops", "WEB_SERVERS") == "Hand edit by ops"

    def test_empty_description(self):
        assert _strip_original_name_prefix("", "WEB_SERVERS") == ""


# ---- Wireless side: mangled-name parsing + description strip --------------


class TestWirelessMangleParsing:
    """Lock the rsplit('__radio', 1) contract for RadioProfile name recovery.

    Mangled form: <hostname>__<vdom>__<profile_name>__radio<N>. We rely on
    rsplit (not split) so profile names containing ``__radio`` still parse —
    the LAST occurrence is the discriminator.
    """

    def test_standard_radio_extraction(self):
        prof, n = "branch-default__radio2".rsplit("__radio", 1)
        assert prof == "branch-default"
        assert int(n) == 2

    def test_profile_name_with_underscores(self):
        prof, n = "lab_indoor__radio1".rsplit("__radio", 1)
        assert prof == "lab_indoor"
        assert int(n) == 1

    def test_radio_3_for_tri_band(self):
        prof, n = "tri-band-ap__radio3".rsplit("__radio", 1)
        assert prof == "tri-band-ap"
        assert int(n) == 3


class TestStripDescriptionPrefix:
    """Wireless-side description prefix strip (mirrors _strip_original_name_prefix)."""

    def _strip(self):
        from nautobot_ssot_fortinet.diffsync.adapters.nautobot_wireless import (
            _strip_description_prefix,
        )

        return _strip_description_prefix

    def test_strips_when_present(self):
        assert self._strip()("corp-wifi: Primary SSID", "corp-wifi") == "Primary SSID"

    def test_returns_empty_when_description_is_only_original_name(self):
        assert self._strip()("corp-wifi", "corp-wifi") == ""

    def test_passes_through_unrelated(self):
        assert self._strip()("Custom text", "corp-wifi") == "Custom text"


# ---- Phase 5: FortiGate-side VIP helpers ---------------------------------


class TestMappedIpToAddressValue:
    """_mapped_ip_to_address_value picks ipaddress vs iprange based on dash presence."""

    def _func(self):
        from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall import (
            _mapped_ip_to_address_value,
        )

        return _mapped_ip_to_address_value

    def test_single_ip_yields_ipaddress(self):
        assert self._func()("10.0.10.5") == ("ipaddress", "10.0.10.5")

    def test_range_yields_iprange_preserving_dash(self):
        assert self._func()("10.0.30.10-10.0.30.20") == ("iprange", "10.0.30.10-10.0.30.20")


class TestUpsertIdempotence:
    """_upsert_address / _upsert_service must not double-add — protects against re-runs."""

    def _adapter(self):
        from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall import (
            FortiGateFirewallAdapter,
        )

        return FortiGateFirewallAdapter(client=MagicMock(), hostname="fgt", vdom="root")

    def test_upsert_address_called_twice_only_adds_once(self):
        a = self._adapter()
        a._upsert_address("fgt__root__vip_X_ext", "ipaddress", "203.0.113.5", "first")
        a._upsert_address("fgt__root__vip_X_ext", "ipaddress", "203.0.113.5", "second")
        assert len(a.get_all("address_object")) == 1

    def test_upsert_service_called_twice_only_adds_once(self):
        a = self._adapter()
        a._upsert_service("VIP_X_ext", "TCP", "8080")
        a._upsert_service("VIP_X_ext", "TCP", "8080")
        assert len(a.get_all("service_object")) == 1
