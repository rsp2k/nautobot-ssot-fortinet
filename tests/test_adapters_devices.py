"""FortiGateDevicesAdapter — VLAN sub-interface + static route loading (v3.1).

These tests run against the FortiGate-side adapter with inline mock data —
no fixture files, no live FortiGate, no Nautobot ORM. They lock in the
v3.1 behavior changes:

1. ``type=vlan`` interfaces are no longer skipped — they flow through
   to DiffSync as ``type='virtual'`` with VLAN attrs populated.
2. ``wqtn.*`` / ``vap.*`` / ``ssl.*`` / ``naf.*`` names are skipped
   regardless of FortiOS type.
3. ``router.static`` is loaded into FortiGateStaticRoute records when
   ``include_static_routes=True`` (the default).
"""

from unittest.mock import MagicMock

import pytest

from nautobot_ssot_fortinet.diffsync.adapters.fortigate_devices import (
    FortiGateDevicesAdapter,
)

# ──────────────────────────────────────────────────────────────────────────────
# Mock client builder
# ──────────────────────────────────────────────────────────────────────────────


def _client_with_interfaces(
    interfaces: list[dict],
    routes: list[dict] | None = None,
    serial: str = "FWF61E0000000000",
    addresses: list[dict] | None = None,
) -> MagicMock:
    """Build a MagicMock client whose system.interface.get() returns the given list.

    v3.2.5+: ``_get_fortios_serial()`` now hits ``.fortigate.get()`` (raw
    HTTP) and reads the ``serial`` field from the envelope JSON, NOT
    ``.fortigate.get_result()`` which strips the envelope and was broken
    against the real list-returning shape of ``system/interface``.

    v3.2.6+: ``addresses=`` supplies the list returned by
    ``cmdb.firewall.address.get(filter=...)`` used by the dstaddr
    resolver path. The mock ignores the filter and returns the full
    list — tests assert on what the resolver picks.
    """
    c = MagicMock()
    c.cmdb.system.interface.get.return_value = interfaces
    c.cmdb.router.static.get.return_value = routes or []
    c.cmdb.firewall.address.get.return_value = addresses or []

    # Mock the raw .get() to return a response object with status_code=200
    # and a .json() method returning the envelope shape FortiOS actually
    # uses: top-level "serial" + "results" dict.
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "serial": serial,
        "version": "v7.0.14",
        "build": 601,
        "results": {"hostname": "test-fortigate", "alias": "test-fortigate"},
    }
    c.fortigate.get.return_value = response
    return c


@pytest.fixture
def base_adapter_kwargs() -> dict:
    return {
        "hostname": "fgt-test",
        "vdom": "root",
        "device_type_model": "FortiWiFi-61E",
        "role_name": "Firewall",
        "location_name": "Lab",
        "status_name": "Active",
    }


# ──────────────────────────────────────────────────────────────────────────────
# VLAN sub-interface loading
# ──────────────────────────────────────────────────────────────────────────────


class TestVlanInterfaceLoading:
    """v3.1: operator VLANs surface as type='virtual' with parent + vlan_id."""

    def test_operator_vlan_is_loaded_with_attrs(self, base_adapter_kwargs):
        ifs = [
            {"name": "internal3", "type": "physical", "vdom": "root", "status": "up"},
            {
                "name": "vlan10",
                "type": "vlan",
                "vdom": "root",
                "status": "up",
                "interface": "internal3",
                "vlanid": 10,
            },
        ]
        adapter = FortiGateDevicesAdapter(client=_client_with_interfaces(ifs), **base_adapter_kwargs)
        adapter.load()
        loaded = {i.name: i for i in adapter.get_all("fortigate_interface")}
        assert "vlan10" in loaded
        v = loaded["vlan10"]
        assert v.type == "virtual"
        assert v.parent_interface_name == "internal3"
        assert v.vlan_id == 10
        assert v.vlan_mode == "tagged"

    def test_quarantine_wqtn_vlan_is_filtered_by_name(self, base_adapter_kwargs):
        ifs = [
            {
                "name": "wqtn.10.guest",
                "type": "vlan",
                "vdom": "root",
                "status": "up",
                "interface": "internal3",
                "vlanid": 10,
            },
            {"name": "wan1", "type": "physical", "vdom": "root", "status": "up"},
        ]
        adapter = FortiGateDevicesAdapter(client=_client_with_interfaces(ifs), **base_adapter_kwargs)
        adapter.load()
        names = {i.name for i in adapter.get_all("fortigate_interface")}
        assert "wqtn.10.guest" not in names
        assert "wan1" in names

    def test_vap_prefixed_interface_filtered(self, base_adapter_kwargs):
        ifs = [
            {"name": "vap.10.corp", "type": "vlan", "vdom": "root", "status": "up", "vlanid": 10},
        ]
        adapter = FortiGateDevicesAdapter(client=_client_with_interfaces(ifs), **base_adapter_kwargs)
        adapter.load()
        assert adapter.get_all("fortigate_interface") == []

    def test_physical_interface_has_no_vlan_attrs(self, base_adapter_kwargs):
        ifs = [
            {"name": "wan1", "type": "physical", "vdom": "root", "status": "up"},
        ]
        adapter = FortiGateDevicesAdapter(client=_client_with_interfaces(ifs), **base_adapter_kwargs)
        adapter.load()
        wan = next(i for i in adapter.get_all("fortigate_interface") if i.name == "wan1")
        assert wan.parent_interface_name == ""
        assert wan.vlan_id is None
        assert wan.vlan_mode == ""

    def test_vlanid_zero_treated_as_no_vlan(self, base_adapter_kwargs):
        """Some FortiOS interfaces report vlanid=0 to mean 'no VLAN'."""
        ifs = [
            {"name": "internal3", "type": "physical", "vdom": "root", "status": "up", "vlanid": 0},
        ]
        adapter = FortiGateDevicesAdapter(client=_client_with_interfaces(ifs), **base_adapter_kwargs)
        adapter.load()
        i = next(i for i in adapter.get_all("fortigate_interface") if i.name == "internal3")
        assert i.vlan_id is None


# ──────────────────────────────────────────────────────────────────────────────
# v3.2.5 — Device.serial extraction (closes v3.0 carryover bug)
# ──────────────────────────────────────────────────────────────────────────────


class TestFortiosSerialExtraction:
    """v3.2.5 fix for the v3.0 carryover where Device.serial was always empty.

    Root cause was the old ``get_result("/cmdb/system/interface?count=1")``
    path: ``get_result()`` strips the envelope (where ``serial`` lives) AND
    crashes on ``dict(results)`` because ``system/interface`` returns a
    LIST. The BLE001-suppressed except hid the crash silently.
    """

    def test_serial_extracted_from_envelope(self, base_adapter_kwargs):
        """The fix: hit raw .get('/cmdb/system/global') and read envelope.serial."""
        client = _client_with_interfaces([], serial="FWF61E1234567890")
        adapter = FortiGateDevicesAdapter(client=client, **base_adapter_kwargs)
        adapter.load()
        dev = next(iter(adapter.get_all("fortigate_device")))
        assert dev.serial == "FWF61E1234567890"

    def test_missing_serial_returns_empty_string(self, base_adapter_kwargs):
        """If the envelope has no serial key, return '' (not None, not a crash)."""
        client = _client_with_interfaces([])
        # Override the canned envelope to drop the serial key
        client.fortigate.get.return_value.json.return_value = {"version": "v7.0.14"}
        adapter = FortiGateDevicesAdapter(client=client, **base_adapter_kwargs)
        adapter.load()
        dev = next(iter(adapter.get_all("fortigate_device")))
        assert dev.serial == ""

    def test_non_200_status_returns_empty(self, base_adapter_kwargs):
        """If system/global returns 4xx/5xx, fall back to empty serial."""
        client = _client_with_interfaces([])
        client.fortigate.get.return_value.status_code = 500
        adapter = FortiGateDevicesAdapter(client=client, **base_adapter_kwargs)
        adapter.load()
        dev = next(iter(adapter.get_all("fortigate_device")))
        assert dev.serial == ""

    def test_get_raises_handled_gracefully(self, base_adapter_kwargs):
        """If the HTTP call itself raises (network drop, timeout), handle it."""
        client = _client_with_interfaces([])
        client.fortigate.get.side_effect = RuntimeError("network unreachable")
        adapter = FortiGateDevicesAdapter(client=client, **base_adapter_kwargs)
        adapter.load()  # Should not raise
        dev = next(iter(adapter.get_all("fortigate_device")))
        assert dev.serial == ""


# ──────────────────────────────────────────────────────────────────────────────
# Static route loading
# ──────────────────────────────────────────────────────────────────────────────


class TestStaticRouteLoading:
    """router.static → FortiGateStaticRoute composite (device, vdom, seq_num)."""

    def test_default_route_loads_with_zero_cidr(self, base_adapter_kwargs):
        routes = [
            {
                "seq-num": 1,
                "dst": "0.0.0.0 0.0.0.0",
                "gateway": "203.0.113.1",
                "device": "wan1",
                "distance": 10,
                "priority": 0,
                "comment": "Default route",
                "vdom": "root",
            }
        ]
        adapter = FortiGateDevicesAdapter(client=_client_with_interfaces([], routes), **base_adapter_kwargs)
        adapter.load()
        loaded = list(adapter.get_all("fortigate_static_route"))
        assert len(loaded) == 1
        r = loaded[0]
        assert r.destination == "0.0.0.0/0"
        assert r.gateway == "203.0.113.1"
        assert r.interface_name == "wan1"
        assert r.seq_num == 1
        assert r.vdom == "root"

    def test_blackhole_route_has_empty_gateway(self, base_adapter_kwargs):
        routes = [
            {
                "seq-num": 5,
                "dst": "192.0.2.0 255.255.255.0",
                "gateway": "0.0.0.0",
                "device": "",
                "blackhole": "enable",
                "vdom": "root",
            }
        ]
        adapter = FortiGateDevicesAdapter(client=_client_with_interfaces([], routes), **base_adapter_kwargs)
        adapter.load()
        r = next(iter(adapter.get_all("fortigate_static_route")))
        assert r.blackhole is True
        assert r.gateway == ""

    def test_non_blackhole_route_blackhole_field_disable_string(self, base_adapter_kwargs):
        """v3.2.2 regression guard — FortiOS returns 'disable' (string), not False.

        Pre-v3.2.2 we did ``bool(raw.get("blackhole", False))`` which
        evaluated ``bool("disable") == True`` — every non-blackhole route
        was misclassified, and its gateway got wiped to '' downstream.
        Caught against fgt-dev's actual default route during live
        validation 2026-05-18.
        """
        routes = [
            {
                "seq-num": 1,
                "dst": "0.0.0.0 0.0.0.0",
                "gateway": "192.168.1.1",
                "device": "wan2",
                "blackhole": "disable",  # ← THE shape that broke v3.1/v3.2
                "vdom": "root",
            }
        ]
        adapter = FortiGateDevicesAdapter(client=_client_with_interfaces([], routes), **base_adapter_kwargs)
        adapter.load()
        r = next(iter(adapter.get_all("fortigate_static_route")))
        assert r.blackhole is False
        assert r.gateway == "192.168.1.1"

    def test_named_address_route_resolves_via_resolver(self, base_adapter_kwargs):
        """v3.2.6+: single-entry dstaddr now resolves via firewall.address lookup."""
        routes = [
            {
                "seq-num": 7,
                "dstaddr": [{"name": "DC_VLANS"}],
                "gateway": "10.1.1.1",
                "device": "internal3",
                "vdom": "root",
            }
        ]
        addresses = [
            {"name": "DC_VLANS", "type": "ipmask", "subnet": "10.20.0.0 255.255.0.0"},
        ]
        adapter = FortiGateDevicesAdapter(
            client=_client_with_interfaces([], routes, addresses=addresses),
            **base_adapter_kwargs,
        )
        adapter.load()
        r = next(iter(adapter.get_all("fortigate_static_route")))
        assert r.destination == "10.20.0.0/16"
        assert r.seq_num == 7

    def test_dstaddr_resolver_skips_unknown_address(self, base_adapter_kwargs):
        """Dstaddr referencing a non-existent address skips the route (not crash)."""
        routes = [
            {"seq-num": 8, "dstaddr": [{"name": "MISSING"}], "gateway": "10.1.1.1", "device": "x", "vdom": "root"}
        ]
        # addresses list is empty — name doesn't resolve
        adapter = FortiGateDevicesAdapter(
            client=_client_with_interfaces([], routes, addresses=[]),
            **base_adapter_kwargs,
        )
        adapter.load()
        assert adapter.get_all("fortigate_static_route") == []

    def test_dstaddr_resolver_skips_fqdn_address(self, base_adapter_kwargs):
        """FQDN addresses can't be represented as a route CIDR — skip with warning."""
        routes = [
            {"seq-num": 9, "dstaddr": [{"name": "vendor.com"}], "gateway": "10.1.1.1", "device": "x", "vdom": "root"}
        ]
        addresses = [{"name": "vendor.com", "type": "fqdn", "fqdn": "vendor.com"}]
        adapter = FortiGateDevicesAdapter(
            client=_client_with_interfaces([], routes, addresses=addresses),
            **base_adapter_kwargs,
        )
        adapter.load()
        assert adapter.get_all("fortigate_static_route") == []

    def test_dstaddr_multi_entry_skipped(self, base_adapter_kwargs):
        """Multi-entry dstaddr is ambiguous (would need multiple Route records)."""
        routes = [
            {
                "seq-num": 10,
                "dstaddr": [{"name": "A"}, {"name": "B"}],
                "gateway": "10.1.1.1",
                "device": "x",
                "vdom": "root",
            }
        ]
        addresses = [
            {"name": "A", "type": "ipmask", "subnet": "10.30.0.0 255.255.255.0"},
            {"name": "B", "type": "ipmask", "subnet": "10.40.0.0 255.255.255.0"},
        ]
        adapter = FortiGateDevicesAdapter(
            client=_client_with_interfaces([], routes, addresses=addresses),
            **base_adapter_kwargs,
        )
        adapter.load()
        assert adapter.get_all("fortigate_static_route") == []

    def test_dstaddr_cache_avoids_repeat_lookups(self, base_adapter_kwargs):
        """Two routes referencing the same address name = 1 firewall.address.get() call."""
        routes = [
            {"seq-num": 11, "dstaddr": [{"name": "SHARED"}], "gateway": "10.1.1.1", "device": "x", "vdom": "root"},
            {"seq-num": 12, "dstaddr": [{"name": "SHARED"}], "gateway": "10.2.2.2", "device": "y", "vdom": "root"},
        ]
        addresses = [{"name": "SHARED", "type": "ipmask", "subnet": "10.50.0.0 255.255.255.0"}]
        client = _client_with_interfaces([], routes, addresses=addresses)
        adapter = FortiGateDevicesAdapter(client=client, **base_adapter_kwargs)
        adapter.load()
        # Both routes should have loaded with the same destination
        loaded = sorted(adapter.get_all("fortigate_static_route"), key=lambda r: r.seq_num)
        assert len(loaded) == 2
        assert loaded[0].destination == "10.50.0.0/24"
        assert loaded[1].destination == "10.50.0.0/24"
        # And firewall.address.get should have been called only ONCE (cache hit on 2nd route)
        assert client.cmdb.firewall.address.get.call_count == 1

    def test_route_in_other_vdom_is_skipped(self, base_adapter_kwargs):
        routes = [
            {"seq-num": 1, "dst": "10.0.0.0 255.255.0.0", "gateway": "10.0.0.1", "device": "x", "vdom": "vsys2"},
        ]
        adapter = FortiGateDevicesAdapter(client=_client_with_interfaces([], routes), **base_adapter_kwargs)
        adapter.load()
        assert adapter.get_all("fortigate_static_route") == []

    def test_include_static_routes_false_skips_loading(self, base_adapter_kwargs):
        routes = [
            {"seq-num": 1, "dst": "0.0.0.0 0.0.0.0", "gateway": "203.0.113.1", "device": "wan1", "vdom": "root"},
        ]
        adapter = FortiGateDevicesAdapter(
            client=_client_with_interfaces([], routes),
            include_static_routes=False,
            **base_adapter_kwargs,
        )
        adapter.load()
        assert adapter.get_all("fortigate_static_route") == []

    def test_route_endpoint_failure_logs_and_continues(self, base_adapter_kwargs):
        """If router.static fails (older FortiOS, permission issue), the sync
        should log a warning rather than crash the whole Device load."""
        c = MagicMock()
        c.cmdb.system.interface.get.return_value = []
        c.cmdb.router.static.get.side_effect = RuntimeError("404 Not Found")
        c.fortigate.get_result.return_value = {"serial": "X"}
        adapter = FortiGateDevicesAdapter(client=c, **base_adapter_kwargs)
        # Should not raise
        adapter.load()
        assert adapter.get_all("fortigate_static_route") == []
