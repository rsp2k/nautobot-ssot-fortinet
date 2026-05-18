"""FortiGate firewall adapter — load() behavior against recorded fixtures.

The adapter is constructed with a MagicMock fortigate-api client; each
``cmdb.firewall.*.get()`` call returns one of the fixture JSON files.
Tests assert on the contents of the DiffSync store after ``load()``.

No Django, no live FortiGate. These tests run on plain ``pytest`` in
under a second.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall import (
    FortiGateFirewallAdapter,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def fortigate_client() -> MagicMock:
    """Mock client whose ``cmdb.firewall.*.get()`` returns fixture data."""
    client = MagicMock()
    client.cmdb.firewall.address.get.return_value = _load_fixture("firewall_address.json")
    client.cmdb.firewall.addrgrp.get.return_value = _load_fixture("firewall_addrgrp.json")
    client.cmdb.firewall_service.custom.get.return_value = _load_fixture("firewall_service_custom.json")
    client.cmdb.firewall_service.group.get.return_value = _load_fixture("firewall_service_group.json")
    client.cmdb.firewall.policy.get.return_value = _load_fixture("firewall_policy.json")
    client.cmdb.firewall.vip.get.return_value = _load_fixture("firewall_vip.json")
    return client


@pytest.fixture
def adapter(fortigate_client: MagicMock) -> FortiGateFirewallAdapter:
    a = FortiGateFirewallAdapter(client=fortigate_client, hostname="fgt-edge1", vdom="root")
    a.load()
    return a


class TestAddressObjectLoad:
    def test_loads_supported_types_skips_unsupported(self, adapter):
        # Filter out VIP-synthesized addresses (those are covered by NAT tests).
        names = sorted(o.name for o in adapter.get_all("address_object") if "__vip_" not in o.name)
        # all 5 supported (all, WEB_SERVERS, DB_HOST_1, VPN_POOL, salesforce.com)
        # are present; GEO_RU and WILD_LAB skipped.
        assert names == sorted(
            [
                "fgt-edge1__root__all",
                "fgt-edge1__root__WEB_SERVERS",
                "fgt-edge1__root__DB_HOST_1",
                "fgt-edge1__root__VPN_POOL",
                "fgt-edge1__root__salesforce.com",
            ]
        )

    def test_ipmask_converts_to_cidr(self, adapter):
        web = adapter.get("address_object", "fgt-edge1__root__WEB_SERVERS")
        assert web.address_type == "ipmask"
        assert web.value == "10.0.10.0/24"
        assert web.original_name == "WEB_SERVERS"
        assert web.description == "Public web tier"

    def test_host_via_32_mask_becomes_ipaddress(self, adapter):
        # v2.5+: /32 ipmask addresses normalize to address_type=ipaddress
        # with bare IP value (no /32 suffix). FortiOS has no separate
        # "host" type — it always stores host IPs as ipmask with full
        # mask — so this normalization is required for push/pull
        # round-trip stability with VIP-synthesized addresses, and
        # aligns with Nautobot's IPAddress semantic for host IPs.
        db = adapter.get("address_object", "fgt-edge1__root__DB_HOST_1")
        assert db.address_type == "ipaddress"
        assert db.value == "10.0.20.5"

    def test_any_address_round_trip(self, adapter):
        all_obj = adapter.get("address_object", "fgt-edge1__root__all")
        assert all_obj.value == "0.0.0.0/0"

    def test_fqdn_keeps_dotted_form(self, adapter):
        sf = adapter.get("address_object", "fgt-edge1__root__salesforce.com")
        assert sf.address_type == "fqdn"
        assert sf.value == "salesforce.com"

    def test_iprange_uses_dash_separator(self, adapter):
        vpn = adapter.get("address_object", "fgt-edge1__root__VPN_POOL")
        assert vpn.address_type == "iprange"
        assert vpn.value == "10.99.0.10-10.99.0.250"
        assert vpn.description == "Remote VPN clients"


class TestAddressGroupLoad:
    def test_groups_loaded_with_mangled_members(self, adapter):
        internal = adapter.get("address_object_group", "fgt-edge1__root__INTERNAL_NETS")
        # Members must be mangled AND sorted — sorting is the canonical form
        # both adapters produce, otherwise diff churns forever (Django M2M is
        # unordered, so the Nautobot adapter side always returns sorted).
        assert internal.members == sorted(
            [
                "fgt-edge1__root__WEB_SERVERS",
                "fgt-edge1__root__DB_HOST_1",
            ]
        )
        assert internal.description == "Trusted internal"

    def test_group_with_fqdn_member(self, adapter):
        saas = adapter.get("address_object_group", "fgt-edge1__root__SAAS_VENDORS")
        assert saas.members == ["fgt-edge1__root__salesforce.com"]


class TestServiceObjectLoad:
    def test_loads_all_services(self, adapter):
        # Filter out VIP-synthesized port-forward services (those have names
        # starting with "VIP_" and are covered by NAT tests).
        names = sorted(o.name for o in adapter.get_all("service_object") if not o.name.startswith("VIP_"))
        assert names == sorted(["HTTP", "HTTPS", "DNS", "PING", "OSPF", "WEB_RANGE"])

    def test_service_names_are_NOT_mangled(self, adapter):
        # ServiceObject has composite NK; no global uniqueness on name.
        http = adapter.get("service_object", {"ip_protocol": "TCP", "port": "80", "name": "HTTP"})
        assert http.name == "HTTP"  # NOT 'fgt-edge1__root__HTTP'

    def test_tcp_service(self, adapter):
        https = adapter.get("service_object", {"ip_protocol": "TCP", "port": "443", "name": "HTTPS"})
        assert https.ip_protocol == "TCP"
        assert https.port == "443"

    def test_dns_picks_tcp_over_udp(self, adapter):
        # DNS fixture has both tcp-portrange and udp-portrange populated;
        # convention is TCP first. The UDP variant is not separately loaded
        # because FortiOS treats it as one service object.
        dns = adapter.get("service_object", {"ip_protocol": "TCP", "port": "53", "name": "DNS"})
        assert dns is not None

    def test_icmp_service(self, adapter):
        ping = adapter.get("service_object", {"ip_protocol": "ICMP", "port": "8", "name": "PING"})
        assert ping.description == "Echo Request"

    def test_ip_protocol_service(self, adapter):
        # OSPF (protocol-number=89) maps to "OSPFIGP" with empty port,
        # matching firewall-models' IP_PROTOCOL_CHOICES enum.
        ospf = adapter.get("service_object", {"ip_protocol": "OSPFIGP", "port": "", "name": "OSPF"})
        assert ospf is not None

    def test_port_range_preserved(self, adapter):
        wr = adapter.get("service_object", {"ip_protocol": "TCP", "port": "8000-8099", "name": "WEB_RANGE"})
        assert wr.description == "Port range"


class TestServiceGroupLoad:
    def test_group_members_are_composite_natural_keys(self, adapter):
        web = adapter.get("service_object_group", "fgt-edge1__root__WEB_SVCS")
        # Sorted for stable diff. Tuple sort: "443" < "80" lexicographically.
        assert web.members == sorted(
            [
                ("TCP", "80", "HTTP"),
                ("TCP", "443", "HTTPS"),
            ]
        )

    def test_dangling_member_is_skipped_not_exploded(self, adapter):
        # The DANGLING group references "NOT_A_REAL_SVC" which doesn't exist.
        # Adapter should drop the reference silently rather than crash.
        dangling = adapter.get("service_object_group", "fgt-edge1__root__DANGLING")
        assert dangling.members == [("TCP", "80", "HTTP")]


class TestVdomScoping:
    def test_different_vdom_yields_different_mangled_names(self, fortigate_client):
        a_root = FortiGateFirewallAdapter(client=fortigate_client, hostname="fgt-1", vdom="root")
        a_dmz = FortiGateFirewallAdapter(client=fortigate_client, hostname="fgt-1", vdom="dmz")
        a_root.load()
        a_dmz.load()

        # Same FortiGate, different VDOMs → no name collisions, even with
        # identical FortiOS object names underneath.
        root_names = {o.name for o in a_root.get_all("address_object")}
        dmz_names = {o.name for o in a_dmz.get_all("address_object")}
        assert root_names.isdisjoint(dmz_names)
        assert "fgt-1__root__WEB_SERVERS" in root_names
        assert "fgt-1__dmz__WEB_SERVERS" in dmz_names


class TestLoadOrderingIndependence:
    def test_get_all_returns_consistent_count(self, adapter):
        # After load(), counts match the fixture sizes minus skipped types,
        # PLUS VIP-synthesized addresses + services. The VIP fixture has 5
        # entries; 4 valid (MISSING_EXTIP skipped) × 2 synthesized addrs each = 8.
        # Of those 4, 2 are port-forwards → 2 × 2 synth services = 4 services added.
        assert len(adapter.get_all("address_object")) == 5 + 8  # firewall + VIP-synth
        assert len(adapter.get_all("address_object_group")) == 2
        assert len(adapter.get_all("service_object")) == 6 + 4  # firewall + VIP-synth
        assert len(adapter.get_all("service_object_group")) == 2
        assert len(adapter.get_all("policy")) == 1
        assert len(adapter.get_all("policy_rule")) == 5
        assert len(adapter.get_all("nat_policy")) == 1
        assert len(adapter.get_all("nat_policy_rule")) == 4  # 5 fixture - 1 invalid


class TestPolicyLoad:
    def test_singleton_policy_created(self, adapter):
        policy = adapter.get("policy", "fgt-edge1__root__policy")
        assert policy.vdom == "root"
        assert policy.hostname == "fgt-edge1"

    def test_simple_rule_with_group_src_and_leaf_dst(self, adapter):
        # policyid=1: srcaddr=[INTERNAL_NETS] (group), dstaddr=[all] (leaf)
        r1 = adapter.get("policy_rule", "fgt-edge1__root__rule_1")
        assert r1.action == "allow"  # FortiOS "accept" → firewall-models "allow"
        assert r1.log is True
        assert r1.index == 1
        assert r1.original_name == "Allow_Internal_Web"
        assert r1.source_address_groups == ["fgt-edge1__root__INTERNAL_NETS"]
        assert r1.source_addresses == []
        assert r1.destination_addresses == ["fgt-edge1__root__all"]
        assert r1.destination_address_groups == []
        # destination services: HTTP, HTTPS — both are ServiceObjects (leaves)
        assert r1.destination_services == sorted(
            [
                ("TCP", "443", "HTTPS"),
                ("TCP", "80", "HTTP"),
            ]
        )
        assert r1.destination_service_groups == []

    def test_deny_rule_with_fqdn_dst_and_service_group(self, adapter):
        # policyid=2: dstaddr=[salesforce.com] (FQDN-typed leaf),
        # service=[WEB_SVCS] (group)
        r2 = adapter.get("policy_rule", "fgt-edge1__root__rule_2")
        assert r2.action == "deny"
        assert r2.destination_addresses == ["fgt-edge1__root__salesforce.com"]
        assert r2.destination_service_groups == ["fgt-edge1__root__WEB_SVCS"]
        assert r2.destination_services == []

    def test_mixed_leaf_and_group_members(self, adapter):
        # policyid=3: srcaddr=[INTERNAL_NETS(grp), SAAS_VENDORS(grp)],
        # dstaddr=[DB_HOST_1(leaf), VPN_POOL(leaf)],
        # service=[PING(leaf), DNS(leaf), WEB_SVCS(grp)]
        r3 = adapter.get("policy_rule", "fgt-edge1__root__rule_3")
        assert r3.source_address_groups == sorted(
            [
                "fgt-edge1__root__INTERNAL_NETS",
                "fgt-edge1__root__SAAS_VENDORS",
            ]
        )
        assert r3.source_addresses == []
        assert r3.destination_addresses == sorted(
            [
                "fgt-edge1__root__DB_HOST_1",
                "fgt-edge1__root__VPN_POOL",
            ]
        )
        assert r3.destination_services == sorted(
            [
                ("TCP", "53", "DNS"),
                ("ICMP", "8", "PING"),
            ]
        )
        assert r3.destination_service_groups == ["fgt-edge1__root__WEB_SVCS"]
        assert r3.log is False  # logtraffic=disable

    def test_ipsec_action_lossy_maps_to_allow_with_note(self, adapter):
        # policyid=4: action=ipsec → allow with annotation in description.
        r4 = adapter.get("policy_rule", "fgt-edge1__root__rule_4")
        assert r4.action == "allow"
        assert "ipsec" in r4.description.lower()
        assert "[srcintf=wan1 dstintf=internal]" in r4.description

    def test_unknown_service_silently_dropped(self, adapter):
        # policyid=5: service=[HTTPS, NONEXISTENT_SVC] → only HTTPS resolved
        r5 = adapter.get("policy_rule", "fgt-edge1__root__rule_5")
        assert r5.destination_services == [("TCP", "443", "HTTPS")]
        assert r5.destination_service_groups == []

    def test_policy_name_attr_links_rule_to_parent(self, adapter):
        # All rules belong to the singleton VDOM Policy.
        for rule in adapter.get_all("policy_rule"):
            assert rule.policy_name == "fgt-edge1__root__policy"


class TestNATPolicyAndVIPLoad:
    def test_singleton_nat_policy_created(self, adapter):
        np = adapter.get("nat_policy", "fgt-edge1__root__nat_policy")
        assert np.vdom == "root"
        assert np.hostname == "fgt-edge1"

    def test_valid_vips_loaded_invalid_skipped(self, adapter):
        # Fixture has 5 VIPs but MISSING_EXTIP gets skipped (no extip).
        # So 4 NATPolicyRules.
        rule_names = sorted(r.name for r in adapter.get_all("nat_policy_rule"))
        assert rule_names == sorted(
            [
                "fgt-edge1__root__nat_rule_WEB_DNAT",
                "fgt-edge1__root__nat_rule_WEB_PORTFWD",
                "fgt-edge1__root__nat_rule_SSH_BASTION",
                "fgt-edge1__root__nat_rule_RANGE_NAT",
            ]
        )

    def test_synthetic_addresses_added_for_each_vip(self, adapter):
        # Each loaded VIP synthesizes _ext and _mapped AddressObjects.
        names = {o.name for o in adapter.get_all("address_object")}
        for vip in ("WEB_DNAT", "WEB_PORTFWD", "SSH_BASTION", "RANGE_NAT"):
            assert f"fgt-edge1__root__vip_{vip}_ext" in names
            assert f"fgt-edge1__root__vip_{vip}_mapped" in names

    def test_dnat_only_rule_no_services(self, adapter):
        # WEB_DNAT has portforward=disable → no services
        r = adapter.get("nat_policy_rule", "fgt-edge1__root__nat_rule_WEB_DNAT")
        assert r.original_destination_addresses == ["fgt-edge1__root__vip_WEB_DNAT_ext"]
        assert r.translated_destination_addresses == ["fgt-edge1__root__vip_WEB_DNAT_mapped"]
        assert r.original_destination_services == []
        assert r.translated_destination_services == []

    def test_portfwd_rule_populates_services(self, adapter):
        # WEB_PORTFWD: TCP 8080 -> 80
        r = adapter.get("nat_policy_rule", "fgt-edge1__root__nat_rule_WEB_PORTFWD")
        assert r.original_destination_services == [("TCP", "8080", "VIP_WEB_PORTFWD_ext")]
        assert r.translated_destination_services == [("TCP", "80", "VIP_WEB_PORTFWD_mapped")]
        assert "portforward TCP 8080 -> 80" in r.description

    def test_range_mappedip_yields_iprange_address(self, adapter):
        # RANGE_NAT: mappedip "10.0.30.10-10.0.30.20"
        mapped_addr = adapter.get("address_object", "fgt-edge1__root__vip_RANGE_NAT_mapped")
        assert mapped_addr.address_type == "iprange"
        assert mapped_addr.value == "10.0.30.10-10.0.30.20"

    def test_single_extip_is_ipaddress_type(self, adapter):
        ext = adapter.get("address_object", "fgt-edge1__root__vip_WEB_DNAT_ext")
        assert ext.address_type == "ipaddress"
        assert ext.value == "203.0.113.5"

    def test_nat_rule_parent_link(self, adapter):
        for r in adapter.get_all("nat_policy_rule"):
            assert r.nat_policy_name == "fgt-edge1__root__nat_policy"
