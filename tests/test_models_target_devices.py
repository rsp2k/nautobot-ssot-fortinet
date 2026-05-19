"""Unit tests for VLAN sub-interface push (v3.3+).

Covers the **safety-critical** push direction. Two classes of tests:

1. Happy-path: VLAN sub-interface create/update/delete builds the right
   FortiOS payloads and invokes the right cmdb endpoints.
2. **Sabotage tests** — try to push a non-VLAN interface, confirm the
   safety guard REFUSES the push before any REST call goes out. These
   are the regression-guards against the "wrong push disconnects the
   appliance" failure mode.
"""

from unittest.mock import MagicMock

import pytest

from nautobot_ssot_fortinet.diffsync.adapters.fortigate_devices_target import (
    FortiGateDevicesTargetAdapter,
)
from nautobot_ssot_fortinet.diffsync.models.fortigate_target_devices import (
    MIN_PUSHABLE_SEQ_NUM,
    FortiGateTargetInterface,
    FortiGateTargetStaticRoute,
    _build_route_payload,
    _build_vlan_payload,
    _is_pushable_route,
    _is_pushable_vlan_interface,
)
from nautobot_ssot_fortinet.utils.fortios import FortiOSAPIError


def _full_vlan_attrs(**overrides):
    """All required FortiGateInterface fields populated; override what you want."""
    base = {
        "type": "virtual",
        "enabled": True,
        "mtu": None,
        "description": "",
        "vdom": "root",
        "cidrs": ["198.51.100.1/24"],
        "parent_interface_name": "wan1",
        "vlan_id": 100,
        "vlan_mode": "tagged",
    }
    base.update(overrides)
    return base


def _full_non_vlan_attrs(**overrides):
    """A physical interface — should be REFUSED by the safety guard."""
    base = {
        "type": "1000base-t",
        "enabled": True,
        "mtu": None,
        "description": "",
        "vdom": "root",
        "cidrs": [],
        "parent_interface_name": "",
        "vlan_id": None,
        "vlan_mode": "",
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────────
# Pure helper tests
# ──────────────────────────────────────────────────────────────────────────────


class TestPushableVlanWhitelist:
    """The whitelist that REFUSES non-VLAN interfaces. Defense in depth."""

    def test_accepts_textbook_vlan(self):
        attrs = {"type": "virtual", "parent_interface_name": "wan1", "vlan_id": 100}
        ok, reason = _is_pushable_vlan_interface(attrs)
        assert ok is True
        assert reason == ""

    def test_refuses_physical_interface(self):
        """A physical port is the worst-case push target — could be management."""
        attrs = {"type": "1000base-t", "parent_interface_name": "", "vlan_id": None}
        ok, reason = _is_pushable_vlan_interface(attrs)
        assert ok is False
        assert "1000base-t" in reason

    def test_refuses_lag_interface(self):
        """Hard-switch / aggregate map to type=lag — refused."""
        attrs = {"type": "lag", "parent_interface_name": "", "vlan_id": None}
        ok, reason = _is_pushable_vlan_interface(attrs)
        assert ok is False
        assert "lag" in reason

    def test_refuses_virtual_without_parent(self):
        """Bare virtual (e.g. loopback) — no parent → not a VLAN sub-interface."""
        attrs = {"type": "virtual", "parent_interface_name": "", "vlan_id": 100}
        ok, reason = _is_pushable_vlan_interface(attrs)
        assert ok is False
        assert "parent" in reason.lower()

    def test_refuses_vlan_id_out_of_range(self):
        """802.1Q VLAN ID range is 1..4094 — outside is invalid."""
        for bad_id in (0, 4095, -1, None, "100"):
            attrs = {"type": "virtual", "parent_interface_name": "wan1", "vlan_id": bad_id}
            ok, reason = _is_pushable_vlan_interface(attrs)
            assert ok is False
            assert "vlan_id" in reason


class TestVlanPayloadBuilder:
    """The FortiOS payload generator — hardcoded safe defaults."""

    def test_basic_payload_shape(self):
        attrs = {
            "type": "virtual",
            "parent_interface_name": "wan1",
            "vlan_id": 100,
            "vdom": "root",
            "enabled": True,
            "description": "Operator notes",
            "cidrs": ["198.51.100.1/24"],
        }
        payload = _build_vlan_payload("vlan100", attrs)
        assert payload["name"] == "vlan100"
        assert payload["type"] == "vlan"
        assert payload["interface"] == "wan1"
        assert payload["vlanid"] == 100
        assert payload["vdom"] == "root"
        assert payload["status"] == "up"
        assert payload["ip"] == "198.51.100.1 255.255.255.0"

    def test_allowaccess_hardcoded_to_ping(self):
        """SECURITY: never enable HTTPS/SSH/SNMP on a synced VLAN.

        Operators wanting management access configure it on FortiOS UI
        after first sync — explicit human action required.
        """
        attrs = {"type": "virtual", "parent_interface_name": "wan1", "vlan_id": 10, "cidrs": []}
        payload = _build_vlan_payload("vlan10", attrs)
        assert payload["allowaccess"] == "ping"

    def test_description_includes_sync_marker(self):
        """Operators must be able to identify Nautobot-managed interfaces at a glance."""
        attrs = {
            "type": "virtual",
            "parent_interface_name": "wan1",
            "vlan_id": 10,
            "description": "My VLAN",
            "cidrs": [],
        }
        payload = _build_vlan_payload("vlan10", attrs)
        assert "[Synced from Nautobot]" in payload["description"]
        assert "My VLAN" in payload["description"]

    def test_disabled_interface_status_down(self):
        attrs = {"type": "virtual", "parent_interface_name": "wan1", "vlan_id": 10, "enabled": False, "cidrs": []}
        payload = _build_vlan_payload("vlan10", attrs)
        assert payload["status"] == "down"

    def test_empty_cidrs_no_ip_field(self):
        attrs = {"type": "virtual", "parent_interface_name": "wan1", "vlan_id": 10, "cidrs": []}
        payload = _build_vlan_payload("vlan10", attrs)
        assert "ip" not in payload

    def test_mtu_override(self):
        attrs = {"type": "virtual", "parent_interface_name": "wan1", "vlan_id": 10, "cidrs": [], "mtu": 1400}
        payload = _build_vlan_payload("vlan10", attrs)
        assert payload["mtu-override"] == "enable"
        assert payload["mtu"] == 1400


# ──────────────────────────────────────────────────────────────────────────────
# CRUD model tests with mocked client
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def target_adapter():
    """Real (empty-store) FortiGateDevicesTargetAdapter with mocked client.

    Same trick the wireless target tests use: skip the parent __init__
    (which wants a client + REST connection) and re-init via diffsync's
    Adapter.__init__ to wire up the empty stores. Then attach our
    MagicMock client + Job logger after.
    """
    from diffsync import Adapter

    a = FortiGateDevicesTargetAdapter.__new__(FortiGateDevicesTargetAdapter)
    Adapter.__init__(a)
    a.client = MagicMock()
    a.hostname = "fgt-test"
    a.vdom = "root"
    a.job = MagicMock()
    response = MagicMock()
    response.status_code = 200
    a.client.cmdb.system.interface.create.return_value = response
    a.client.cmdb.system.interface.update.return_value = response
    a.client.cmdb.system.interface.delete.return_value = response
    return a


class TestVlanCreate:
    def test_create_invokes_fortios_with_correct_payload(self, target_adapter):
        ids = {"device_name": "fgt-test", "name": "vlan100"}
        attrs = _full_vlan_attrs()
        FortiGateTargetInterface.create(target_adapter, ids, attrs)
        target_adapter.client.cmdb.system.interface.create.assert_called_once()
        payload = target_adapter.client.cmdb.system.interface.create.call_args.kwargs["data"]
        assert payload["name"] == "vlan100"
        assert payload["interface"] == "wan1"
        assert payload["vlanid"] == 100
        assert payload["allowaccess"] == "ping"


class TestSabotagePhysicalInterfacePush:
    """**Critical safety regression guards.** Try to push a non-VLAN
    interface — the safety guard MUST refuse before any REST call.
    """

    def test_physical_interface_create_skipped_no_rest_call(self, target_adapter):
        """Sabotage: try to push a physical port."""
        ids = {"device_name": "fgt-test", "name": "wan1"}
        attrs = _full_non_vlan_attrs()
        FortiGateTargetInterface.create(target_adapter, ids, attrs)
        # ZERO REST calls — safety guard caught it
        target_adapter.client.cmdb.system.interface.create.assert_not_called()
        target_adapter.job.logger.warning.assert_called()

    def test_lag_interface_create_skipped(self, target_adapter):
        ids = {"device_name": "fgt-test", "name": "internal"}
        attrs = _full_non_vlan_attrs(type="lag")
        FortiGateTargetInterface.create(target_adapter, ids, attrs)
        target_adapter.client.cmdb.system.interface.create.assert_not_called()

    def test_physical_interface_delete_raises(self, target_adapter):
        """Delete is the most dangerous — safety guard raises explicitly.

        FortiOSAPIError surfaces to the Job log so operators see a clear
        "refused" message rather than silent skip.
        """
        instance = FortiGateTargetInterface(
            device_name="fgt-test",
            name="wan1",
            type="1000base-t",  # NOT virtual
            enabled=True,
            mtu=None,
            description="",
            vdom="root",
            cidrs=[],
            parent_interface_name="",
            vlan_id=None,
            vlan_mode="",
        )
        instance.adapter = target_adapter
        with pytest.raises(FortiOSAPIError, match="refusing to push"):
            instance.delete()
        target_adapter.client.cmdb.system.interface.delete.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# v3.4: Static route push — happy path + sabotage tests
# ──────────────────────────────────────────────────────────────────────────────


def _full_route_attrs(**overrides):
    """Default pushable route — destination 198.51.100.0/24 via wan2."""
    base = {
        "destination": "198.51.100.0/24",
        "gateway": "192.168.1.1",
        "interface_name": "wan2",
        "distance": 10,
        "priority": 0,
        "blackhole": False,
        "comment": "",
    }
    base.update(overrides)
    return base


@pytest.fixture
def route_adapter():
    """Adapter for route push tests with mocked router.static endpoint."""
    from diffsync import Adapter

    a = FortiGateDevicesTargetAdapter.__new__(FortiGateDevicesTargetAdapter)
    Adapter.__init__(a)
    a.client = MagicMock()
    a.hostname = "fgt-test"
    a.vdom = "root"
    a.job = MagicMock()
    response = MagicMock()
    response.status_code = 200
    a.client.cmdb.router.static.create.return_value = response
    a.client.cmdb.router.static.update.return_value = response
    a.client.cmdb.router.static.delete.return_value = response
    return a


class TestPushableRouteWhitelist:
    """Whitelist that REFUSES operator-territory + blackhole routes."""

    def test_accepts_textbook_route(self):
        ok, reason = _is_pushable_route(_full_route_attrs(), seq_num=9001)
        assert ok is True
        assert reason == ""

    def test_refuses_low_seq_route(self):
        """seq < MIN_PUSHABLE_SEQ_NUM = operator-managed territory."""
        for seq in (1, 5, 100, 999):
            ok, reason = _is_pushable_route(_full_route_attrs(), seq_num=seq)
            assert ok is False
            assert "MIN_PUSHABLE_SEQ_NUM" in reason

    def test_min_pushable_seq_constant(self):
        """The threshold is documented and centralised."""
        assert MIN_PUSHABLE_SEQ_NUM == 1000

    def test_refuses_blackhole_route(self):
        """Blackholes are usually intentional security policy — operators set manually."""
        attrs = _full_route_attrs(blackhole=True)
        ok, reason = _is_pushable_route(attrs, seq_num=9001)
        assert ok is False
        assert "blackhole" in reason

    def test_refuses_no_destination(self):
        attrs = _full_route_attrs(destination="")
        ok, reason = _is_pushable_route(attrs, seq_num=9001)
        assert ok is False
        assert "destination" in reason

    def test_refuses_no_gateway_and_no_interface(self):
        """Route with no gateway AND no egress interface has nowhere to send traffic."""
        attrs = _full_route_attrs(gateway="", interface_name="")
        ok, reason = _is_pushable_route(attrs, seq_num=9001)
        assert ok is False

    def test_accepts_gateway_only(self):
        """Routes with just a gateway (no interface) are valid — FortiOS resolves egress via RIB."""
        attrs = _full_route_attrs(interface_name="")
        ok, _ = _is_pushable_route(attrs, seq_num=9001)
        assert ok is True

    def test_accepts_interface_only(self):
        """Routes with just an interface (no gateway) are valid for connected networks."""
        attrs = _full_route_attrs(gateway="")
        ok, _ = _is_pushable_route(attrs, seq_num=9001)
        assert ok is True


class TestRoutePayloadBuilder:
    """FortiOS router.static payload generator — sync marker + dotted-mask conversion."""

    def test_destination_converts_to_dotted_mask(self):
        attrs = _full_route_attrs()
        payload = _build_route_payload(9001, attrs)
        assert payload["dst"] == "198.51.100.0 255.255.255.0"

    def test_sync_marker_in_comment(self):
        attrs = _full_route_attrs(comment="Operator notes")
        payload = _build_route_payload(9001, attrs)
        assert "[Synced from Nautobot]" in payload["comment"]
        assert "Operator notes" in payload["comment"]

    def test_seq_num_in_payload(self):
        payload = _build_route_payload(9042, _full_route_attrs())
        assert payload["seq-num"] == 9042

    def test_default_route_cidr_converts(self):
        attrs = _full_route_attrs(destination="0.0.0.0/0")
        payload = _build_route_payload(9001, attrs)
        assert payload["dst"] == "0.0.0.0 0.0.0.0"

    def test_optional_fields_omitted_when_empty(self):
        """gateway/device only present when populated — empty would set them empty on FortiOS."""
        attrs = _full_route_attrs(interface_name="")
        payload = _build_route_payload(9001, attrs)
        assert "gateway" in payload
        assert "device" not in payload


class TestRoutePushCRUD:
    def test_create_invokes_fortios_with_correct_payload(self, route_adapter):
        ids = {"device_name": "fgt-test", "vdom": "root", "seq_num": 9001}
        FortiGateTargetStaticRoute.create(route_adapter, ids, _full_route_attrs())
        route_adapter.client.cmdb.router.static.create.assert_called_once()
        payload = route_adapter.client.cmdb.router.static.create.call_args.kwargs["data"]
        assert payload["seq-num"] == 9001
        assert payload["dst"] == "198.51.100.0 255.255.255.0"
        assert payload["gateway"] == "192.168.1.1"


class TestSabotageRoutePush:
    """**Critical safety regression guards for routes.**"""

    def test_low_seq_create_skipped_no_rest_call(self, route_adapter):
        """Sabotage: try to push a route in operator territory (seq < 1000)."""
        ids = {"device_name": "fgt-test", "vdom": "root", "seq_num": 5}
        FortiGateTargetStaticRoute.create(route_adapter, ids, _full_route_attrs())
        route_adapter.client.cmdb.router.static.create.assert_not_called()
        route_adapter.job.logger.warning.assert_called()

    def test_blackhole_create_skipped(self, route_adapter):
        """Sabotage: try to push a blackhole route via sync."""
        ids = {"device_name": "fgt-test", "vdom": "root", "seq_num": 9001}
        attrs = _full_route_attrs(blackhole=True)
        FortiGateTargetStaticRoute.create(route_adapter, ids, attrs)
        route_adapter.client.cmdb.router.static.create.assert_not_called()

    def test_low_seq_delete_raises(self, route_adapter):
        """Delete of a low-seq route MUST raise FortiOSAPIError (not silent skip)."""
        instance = FortiGateTargetStaticRoute(
            device_name="fgt-test",
            vdom="root",
            seq_num=42,  # operator territory
            destination="10.0.0.0/8",
            gateway="10.0.0.1",
            interface_name="wan1",
            distance=10,
            priority=0,
            blackhole=False,
            comment="",
        )
        instance.adapter = route_adapter
        with pytest.raises(FortiOSAPIError, match="MIN_PUSHABLE_SEQ_NUM"):
            instance.delete()
        route_adapter.client.cmdb.router.static.delete.assert_not_called()

    def test_blackhole_delete_raises(self, route_adapter):
        """Delete of a blackhole route MUST raise (don't silently remove security policy)."""
        instance = FortiGateTargetStaticRoute(
            device_name="fgt-test",
            vdom="root",
            seq_num=9001,
            destination="192.0.2.0/24",
            gateway="",
            interface_name="",
            distance=10,
            priority=0,
            blackhole=True,
            comment="",
        )
        instance.adapter = route_adapter
        with pytest.raises(FortiOSAPIError, match="blackhole"):
            instance.delete()
        route_adapter.client.cmdb.router.static.delete.assert_not_called()
