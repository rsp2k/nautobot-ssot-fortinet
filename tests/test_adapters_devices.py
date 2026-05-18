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


def _client_with_interfaces(interfaces: list[dict], routes: list[dict] | None = None) -> MagicMock:
    """Build a MagicMock client whose system.interface.get() returns the given list."""
    c = MagicMock()
    c.cmdb.system.interface.get.return_value = interfaces
    c.cmdb.router.static.get.return_value = routes or []
    # _get_fortios_serial uses .fortigate.get_result() — return a stable serial.
    c.fortigate.get_result.return_value = {"serial": "FGT-TEST-12345"}
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

    def test_named_address_route_is_skipped(self, base_adapter_kwargs):
        """Routes using dstaddr (named address object) are skipped in v3.1."""
        routes = [
            {
                "seq-num": 7,
                "dstaddr": [{"name": "DC_VLANS"}],
                "gateway": "10.1.1.1",
                "device": "internal3",
                "vdom": "root",
            }
        ]
        adapter = FortiGateDevicesAdapter(client=_client_with_interfaces([], routes), **base_adapter_kwargs)
        adapter.load()
        assert adapter.get_all("fortigate_static_route") == []

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
