"""Pure-function unit tests for utils.fortios — no Django, no fixtures."""

import pytest

from nautobot_ssot_fortinet.utils.fortios import (
    IP_PROTOCOL_NAME_TO_NUMBER,
    IP_PROTOCOL_NUMBER_TO_NAME,
    build_fortios_service_payload,
    denormalize_port_separators,
    fortios_action,
    fortios_band_to_frequency,
    fortios_platform_mode_to_network_mode,
    fortios_security_to_auth,
    fortios_service_ports,
    fortios_subnet_to_cidr,
    mangle_name,
    parse_intf_annotation,
    split_policy_members,
)


class TestMangleName:
    def test_basic_three_segment_join(self) -> None:
        assert mangle_name("fgt-edge1", "root", "WEB_SERVERS") == "fgt-edge1__root__WEB_SERVERS"

    def test_preserves_special_chars_in_original(self) -> None:
        # FortiOS allows dots, hyphens, underscores; we don't escape them.
        assert mangle_name("fgt-1", "vdom-prod", "svc.app-01") == "fgt-1__vdom-prod__svc.app-01"


class TestFortiosSubnetToCidr:
    def test_class_c_network(self) -> None:
        assert fortios_subnet_to_cidr("10.0.0.0 255.255.255.0") == "10.0.0.0/24"

    def test_any_address(self) -> None:
        assert fortios_subnet_to_cidr("0.0.0.0 0.0.0.0") == "0.0.0.0/0"

    def test_single_host_via_32_mask(self) -> None:
        assert fortios_subnet_to_cidr("192.168.1.5 255.255.255.255") == "192.168.1.5/32"

    def test_supernet_mask(self) -> None:
        assert fortios_subnet_to_cidr("10.0.0.0 255.255.0.0") == "10.0.0.0/16"

    def test_non_canonical_address_gets_normalized(self) -> None:
        # 10.0.0.5 / 24 is technically host-in-network; strict=False normalizes
        # to 10.0.0.0/24. FortiOS occasionally emits non-canonical pairs.
        assert fortios_subnet_to_cidr("10.0.0.5 255.255.255.0") == "10.0.0.0/24"

    def test_malformed_input_raises(self) -> None:
        with pytest.raises(ValueError, match="expected 'address mask' format"):
            fortios_subnet_to_cidr("10.0.0.0")
        with pytest.raises(ValueError):
            fortios_subnet_to_cidr("not an ip 255.255.255.0")


class TestFortiosServicePorts:
    def test_tcp_only(self) -> None:
        assert fortios_service_ports({"protocol": "TCP/UDP/SCTP", "tcp-portrange": "443", "udp-portrange": ""}) == (
            "TCP",
            "443",
        )

    def test_udp_only(self) -> None:
        assert fortios_service_ports({"protocol": "TCP/UDP/SCTP", "tcp-portrange": "", "udp-portrange": "514"}) == (
            "UDP",
            "514",
        )

    def test_picks_tcp_first_when_both_populated(self) -> None:
        # DNS is the canonical example — same port for TCP and UDP.
        assert fortios_service_ports({"protocol": "TCP/UDP/SCTP", "tcp-portrange": "53", "udp-portrange": "53"}) == (
            "TCP",
            "53",
        )

    def test_icmp_returns_type_number_as_port(self) -> None:
        assert fortios_service_ports({"protocol": "ICMP", "icmptype": 8}) == ("ICMP", "8")

    def test_icmp_with_no_type(self) -> None:
        assert fortios_service_ports({"protocol": "ICMP"}) == ("ICMP", "")

    def test_ip_protocol_ospf_maps_to_iana_name(self) -> None:
        # FortiOS reports OSPF as protocol="IP" + protocol-number=89; the
        # firewall-models choices use the IANA name "OSPFIGP", not "OSPF".
        # Port is empty for protocols without port concept.
        assert fortios_service_ports({"protocol": "IP", "protocol-number": 89}) == ("OSPFIGP", "")

    def test_ip_protocol_gre_maps_to_gre(self) -> None:
        assert fortios_service_ports({"protocol": "IP", "protocol-number": 47}) == ("GRE", "")

    def test_unknown_ip_protocol_number_returns_none(self) -> None:
        # Returning None signals the adapter to skip + log, not crash.
        assert fortios_service_ports({"protocol": "IP", "protocol-number": 254}) == (None, "")

    def test_port_range_preserved_verbatim(self) -> None:
        # We don't expand "8000-8099" — Nautobot stores the FortiOS string as-is.
        assert fortios_service_ports({"protocol": "TCP/UDP/SCTP", "tcp-portrange": "8000-8099"}) == ("TCP", "8000-8099")

    def test_multiple_ports_space_converted_to_comma(self) -> None:
        # FortiOS uses space-separated multi-port lists (e.g. KERBEROS
        # "88 464"). firewall-models' validate_port splits on COMMA — so we
        # normalize at adapter-load time. This is THE bug the live e2e
        # discovered against the FWF-61E's built-in KERBEROS service.
        assert fortios_service_ports({"protocol": "TCP/UDP/SCTP", "tcp-portrange": "88 464"}) == ("TCP", "88,464")

    def test_icmp6_maps_to_ipv6_icmp(self) -> None:
        # FortiOS spells it ICMP6; firewall-models uses IANA's "IPv6-ICMP".
        assert fortios_service_ports({"protocol": "ICMP6", "icmptype": 128}) == ("IPv6-ICMP", "128")

    def test_icmp6_no_type_returns_empty_port(self) -> None:
        assert fortios_service_ports({"protocol": "ICMP6"}) == ("IPv6-ICMP", "")

    def test_rlogin_src_port_qualifier_dropped(self) -> None:
        # RLOGIN: FortiOS says "dst 513, src 512-1023" as "513:512-1023".
        # Nautobot has no source-port concept; we keep only dst.
        assert fortios_service_ports({"protocol": "TCP/UDP/SCTP", "tcp-portrange": "513:512-1023"}) == ("TCP", "513")

    def test_all_pseudoprotocol_skipped(self) -> None:
        # FortiOS "ALL" pseudo-protocol (built-in webproxy svc) has no
        # firewall-models equivalent — return None so caller skips.
        assert fortios_service_ports({"protocol": "ALL"}) == (None, "")

    def test_empty_protocol_falls_back_to_tcp(self) -> None:
        # Defensive — FortiOS shouldn't omit protocol, but we don't crash.
        assert fortios_service_ports({}) == ("TCP", "")


class TestFortiosAction:
    def test_accept_to_allow_no_note(self):
        assert fortios_action("accept") == ("allow", None)

    def test_permit_alias(self):
        assert fortios_action("permit") == ("allow", None)

    def test_deny_clean_mapping(self):
        assert fortios_action("deny") == ("deny", None)

    def test_ipsec_lossy_with_note(self):
        mapped, note = fortios_action("ipsec")
        assert mapped == "allow"
        assert note is not None and "ipsec" in note.lower()

    def test_unknown_action_falls_back_to_deny_with_warning(self):
        mapped, note = fortios_action("totally-bogus")
        assert mapped == "deny"
        assert note is not None and "fallback" in note.lower()

    def test_case_insensitive(self):
        assert fortios_action("ACCEPT") == ("allow", None)


class TestSplitPolicyMembers:
    @staticmethod
    def _no_mangle(n):
        return n

    def test_pure_leaves(self):
        leaves, groups = split_policy_members(
            [{"name": "A"}, {"name": "B"}],
            leaf_names={"A", "B", "C"},
            group_names=set(),
            mangler=self._no_mangle,
        )
        assert leaves == ["A", "B"]
        assert groups == []

    def test_pure_groups(self):
        leaves, groups = split_policy_members(
            [{"name": "G1"}, {"name": "G2"}],
            leaf_names=set(),
            group_names={"G1", "G2"},
            mangler=self._no_mangle,
        )
        assert leaves == []
        assert groups == ["G1", "G2"]

    def test_mixed_split(self):
        leaves, groups = split_policy_members(
            [{"name": "A"}, {"name": "G1"}, {"name": "B"}],
            leaf_names={"A", "B"},
            group_names={"G1"},
            mangler=self._no_mangle,
        )
        assert leaves == ["A", "B"]  # sorted
        assert groups == ["G1"]

    def test_unknown_silently_dropped(self):
        leaves, groups = split_policy_members(
            [{"name": "A"}, {"name": "PHANTOM"}],
            leaf_names={"A"},
            group_names=set(),
            mangler=self._no_mangle,
        )
        assert leaves == ["A"]
        assert groups == []

    def test_mangler_is_applied(self):
        def m(n):
            return f"X__{n}"

        leaves, groups = split_policy_members(
            [{"name": "A"}, {"name": "G1"}],
            leaf_names={"X__A"},
            group_names={"X__G1"},
            mangler=m,
        )
        assert leaves == ["X__A"]
        assert groups == ["X__G1"]


class TestFortiosSecurityToAuth:
    def test_wpa2_personal(self):
        assert fortios_security_to_auth("wpa2-only-personal") == ("WPA2 Personal", None)

    def test_wpa3_sae(self):
        assert fortios_security_to_auth("wpa3-sae") == ("WPA3 SAE", None)

    def test_open(self):
        assert fortios_security_to_auth("open") == ("Open", None)

    def test_wpa3_enterprise_192bit(self):
        assert fortios_security_to_auth("wpa3-only-enterprise-192") == (
            "WPA3 Enterprise 192Bit",
            None,
        )

    def test_enhanced_open_owe(self):
        assert fortios_security_to_auth("owe") == ("Enhanced Open", None)

    def test_wep_lossy_fallback(self):
        mapped, note = fortios_security_to_auth("wep128")
        assert mapped == "Open"
        assert note is not None and "wep128" in note

    def test_unknown_security_falls_back(self):
        mapped, note = fortios_security_to_auth("totally-fake-mode")
        assert mapped == "Open"
        assert note is not None and "totally-fake-mode" in note

    def test_case_insensitive(self):
        assert fortios_security_to_auth("WPA2-ONLY-PERSONAL") == ("WPA2 Personal", None)

    def test_empty_or_none(self):
        # Defensive: missing security field
        assert fortios_security_to_auth("")[0] == "Open"


class TestFortiosPlatformModeToNetworkMode:
    def test_tunnel_to_central(self):
        assert fortios_platform_mode_to_network_mode("FortiAP-tunnel-mode") == "Central"

    def test_local_to_local_flex(self):
        assert fortios_platform_mode_to_network_mode("FortiAP-local-mode") == "Local (Flex)"

    def test_mesh(self):
        assert fortios_platform_mode_to_network_mode("wpa-mesh-mode") == "Mesh"

    def test_unknown_defaults_to_central(self):
        assert fortios_platform_mode_to_network_mode("something-else") == "Central"

    def test_empty(self):
        assert fortios_platform_mode_to_network_mode("") == "Central"


class TestFortiosBandToFrequency:
    def test_5g_ax(self):
        assert fortios_band_to_frequency("802.11ax-5G") == "5GHz"

    def test_2_4g_ng(self):
        assert fortios_band_to_frequency("802.11n,g-only") == "2.4GHz"

    def test_6g_ax(self):
        assert fortios_band_to_frequency("802.11ax-6G") == "6GHz"

    def test_legacy_g(self):
        assert fortios_band_to_frequency("802.11g") == "2.4GHz"

    def test_legacy_n_defaults_to_2_4(self):
        # 802.11n exists on both bands; FortiOS default is 2.4 when unsuffixed
        assert fortios_band_to_frequency("802.11n") == "2.4GHz"

    def test_ac_implies_5g(self):
        assert fortios_band_to_frequency("802.11ac") == "5GHz"

    def test_ax_unsuffixed_defaults_5g(self):
        assert fortios_band_to_frequency("802.11ax") == "5GHz"

    def test_disabled_returns_none(self):
        assert fortios_band_to_frequency("disabled") is None

    def test_empty_returns_none(self):
        assert fortios_band_to_frequency("") is None


# ---- Push-direction reverse helpers ---------------------------------------


class TestDenormalizePortSeparators:
    """Inverse of _normalize_port_separators — Nautobot comma → FortiOS space."""

    def test_multi_port_comma_to_space(self):
        assert denormalize_port_separators("88,464") == "88 464"

    def test_single_port_unchanged(self):
        assert denormalize_port_separators("80") == "80"

    def test_range_unchanged(self):
        assert denormalize_port_separators("8000-8099") == "8000-8099"

    def test_three_port_list(self):
        assert denormalize_port_separators("80,443,993") == "80 443 993"


class TestPortRoundTrip:
    """Identity property: denormalize(normalize(x)) == x for valid FortiOS port shapes."""

    def test_kerberos_round_trip(self):
        from nautobot_ssot_fortinet.utils.fortios import _normalize_port_separators

        original_fortios = "88 464"
        assert denormalize_port_separators(_normalize_port_separators(original_fortios)) == original_fortios

    def test_single_port_round_trip(self):

        for p in ("80", "443", "8000-8099"):
            from nautobot_ssot_fortinet.utils.fortios import _normalize_port_separators as norm

            assert denormalize_port_separators(norm(p)) == p


class TestIpProtocolNameToNumber:
    """Inverse table must be consistent with the forward table."""

    def test_inverse_covers_all_forward_entries(self):
        for num, name in IP_PROTOCOL_NUMBER_TO_NAME.items():
            assert IP_PROTOCOL_NAME_TO_NUMBER[name] == num

    def test_specific_mappings(self):
        assert IP_PROTOCOL_NAME_TO_NUMBER["TCP"] == 6
        assert IP_PROTOCOL_NAME_TO_NUMBER["UDP"] == 17
        assert IP_PROTOCOL_NAME_TO_NUMBER["OSPFIGP"] == 89
        assert IP_PROTOCOL_NAME_TO_NUMBER["GRE"] == 47


class TestBuildFortiosServicePayload:
    """Inverse of fortios_service_ports — produces a valid FortiOS payload."""

    def test_tcp_simple(self):
        p = build_fortios_service_payload("HTTPS", "TCP", "443")
        assert p == {
            "name": "HTTPS",
            "comment": "",
            "protocol": "TCP/UDP/SCTP",
            "tcp-portrange": "443",
        }

    def test_tcp_multi_port_denormalizes(self):
        # Nautobot comma → FortiOS space
        p = build_fortios_service_payload("KERBEROS", "TCP", "88,464")
        assert p["tcp-portrange"] == "88 464"

    def test_udp_uses_udp_portrange(self):
        p = build_fortios_service_payload("DNS_UDP", "UDP", "53")
        assert p["protocol"] == "TCP/UDP/SCTP"
        assert p["udp-portrange"] == "53"
        assert "tcp-portrange" not in p

    def test_sctp(self):
        p = build_fortios_service_payload("SCTP_TEST", "SCTP", "5000")
        assert p["sctp-portrange"] == "5000"

    def test_icmp_port_as_icmptype(self):
        p = build_fortios_service_payload("PING", "ICMP", "8")
        assert p == {"name": "PING", "comment": "", "protocol": "ICMP", "icmptype": 8}

    def test_icmp_no_port(self):
        p = build_fortios_service_payload("ALL_ICMP", "ICMP", "")
        assert p["protocol"] == "ICMP"
        assert "icmptype" not in p

    def test_icmpv6_maps_back_to_icmp6(self):
        # Pull side translates ICMP6→IPv6-ICMP; push must reverse.
        p = build_fortios_service_payload("PING6", "IPv6-ICMP", "128")
        assert p["protocol"] == "ICMP6"
        assert p["icmptype"] == 128

    def test_named_ip_protocol_to_protocol_number(self):
        # OSPF: IPv6-named "OSPFIGP" → FortiOS protocol=IP + protocol-number=89
        p = build_fortios_service_payload("OSPF", "OSPFIGP", "")
        assert p == {"name": "OSPF", "comment": "", "protocol": "IP", "protocol-number": 89}

    def test_gre_round_trip(self):
        p = build_fortios_service_payload("GRE_SVC", "GRE", "")
        assert p["protocol"] == "IP"
        assert p["protocol-number"] == 47

    def test_unknown_protocol_returns_none(self):
        assert build_fortios_service_payload("X", "TOTALLY_BOGUS", "") is None

    def test_description_truncated(self):
        long_desc = "x" * 500
        p = build_fortios_service_payload("HTTPS", "TCP", "443", description=long_desc)
        assert len(p["comment"]) == 255

    def test_service_round_trip(self):
        """Pull(push(x)) == x: payload built from our DiffSync attrs should
        re-parse back to the same (ip_protocol, port) tuple."""
        cases = [
            ("TCP", "443"),
            ("UDP", "53"),
            ("TCP", "88,464"),
            ("ICMP", "8"),
            ("IPv6-ICMP", "128"),
            ("OSPFIGP", ""),
            ("GRE", ""),
        ]
        for ip_protocol, port in cases:
            payload = build_fortios_service_payload("X", ip_protocol, port)
            assert payload is not None, f"No payload for {ip_protocol}"
            parsed_proto, parsed_port = fortios_service_ports(payload)
            assert parsed_proto == ip_protocol, f"{ip_protocol!r} round-tripped to {parsed_proto!r}"
            assert parsed_port == port, f"port {port!r} round-tripped to {parsed_port!r}"


class TestParseIntfAnnotation:
    """v2.1+: reverse the [srcintf=X,Y dstintf=Z] description annotation."""

    def test_single_srcintf(self):
        assert parse_intf_annotation("Internal users [srcintf=lan dstintf=wan1]", "srcintf") == ["lan"]

    def test_single_dstintf(self):
        assert parse_intf_annotation("Internal users [srcintf=lan dstintf=wan1]", "dstintf") == ["wan1"]

    def test_multi_value_comma_separated(self):
        assert parse_intf_annotation("[srcintf=lan,vlan10,vlan20 dstintf=wan1]", "srcintf") == [
            "lan",
            "vlan10",
            "vlan20",
        ]

    def test_extintf_for_nat(self):
        # NAT/VIP uses [extintf=X]; same parser works.
        assert parse_intf_annotation("[extintf=wan1] [portforward TCP 8080 -> 80]", "extintf") == ["wan1"]

    def test_no_annotation_returns_empty_list(self):
        assert parse_intf_annotation("just a free-text comment", "srcintf") == []

    def test_empty_description(self):
        assert parse_intf_annotation("", "srcintf") == []

    def test_dash_placeholder_yields_dash_not_empty(self):
        # When the pull side has no interfaces it emits "-" as a literal
        # placeholder. parse_intf_annotation preserves that; the caller
        # decides whether to treat "-" as a special value.
        assert parse_intf_annotation("[srcintf=- dstintf=wan1]", "srcintf") == ["-"]

    def test_annotation_at_end_of_string(self):
        # The regex must handle annotation at the end (no trailing space).
        assert parse_intf_annotation("a comment [srcintf=lan]", "srcintf") == ["lan"]

    def test_round_trip_with_pull_side_format(self):
        # Pull side emits exactly: [srcintf=A,B dstintf=C,D]
        # The Nautobot side load should pull them back cleanly.
        desc = "Allow web [srcintf=internal,vlan100 dstintf=wan1,wan2]"
        assert parse_intf_annotation(desc, "srcintf") == ["internal", "vlan100"]
        assert parse_intf_annotation(desc, "dstintf") == ["wan1", "wan2"]


# ---------------------------------------------------------------------------
# check_fortios_response — added in v2.4 after silent-500 bug
# ---------------------------------------------------------------------------


class TestCheckFortiOSResponse:
    """Guard against the v1.0-v2.3 silent-500 pattern.

    Pre-v2.4 the model code did ``adapter.client.cmdb.xxx.create(data=...)``
    and discarded the Response. FortiOS uses 500 + ``status: error,
    error: -1`` for validation rejections — silently lost.
    """

    def _resp(self, status_code: int, body: dict | None = None, text: str = "") -> object:
        class _R:
            def __init__(s):
                s.status_code = status_code
                s.text = text

            def json(s):
                if body is None:
                    raise ValueError("no json")
                return body

        return _R()

    def test_passes_through_on_200(self):
        from nautobot_ssot_fortinet.utils.fortios import check_fortios_response

        resp = self._resp(200, {"status": "success"})
        assert check_fortios_response(resp, label="x") is resp

    def test_raises_on_500_with_body_summary(self):
        from nautobot_ssot_fortinet.utils.fortios import FortiOSAPIError, check_fortios_response

        resp = self._resp(500, {"status": "error", "error": -1, "cli_error": "bad shape"})
        with pytest.raises(FortiOSAPIError) as exc:
            check_fortios_response(resp, label="wtp_profile.create 'guest'")
        msg = str(exc.value)
        assert "wtp_profile.create 'guest'" in msg
        assert "500" in msg
        assert "-1" in msg
        assert "bad shape" in msg

    def test_raises_on_non_json_body(self):
        from nautobot_ssot_fortinet.utils.fortios import FortiOSAPIError, check_fortios_response

        resp = self._resp(503, body=None, text="<html>503 Service Unavailable</html>")
        with pytest.raises(FortiOSAPIError) as exc:
            check_fortios_response(resp, label="address.update 'x'")
        msg = str(exc.value)
        assert "address.update 'x'" in msg
        assert "503" in msg
        assert "Service Unavailable" in msg

    def test_raises_on_object_without_status_code(self):
        from nautobot_ssot_fortinet.utils.fortios import FortiOSAPIError, check_fortios_response

        class Weird:
            text = ""

            def json(self):
                return {}

        with pytest.raises(FortiOSAPIError):
            check_fortios_response(Weird(), label="x")


# ---------------------------------------------------------------------------
# strip_pull_annotations — added in v2.5 to fix round-trip annotation dup
# ---------------------------------------------------------------------------


class TestStripPullAnnotations:
    """Pull adapter appends [srcintf=...]/[extintf=...] to descriptions.
    When pushed back as a comment, that annotation lives on FortiOS. On
    next pull, the appender re-adds it → duplication. This strips
    machine-generated annotations before re-appending so the round-trip
    is stable.
    """

    def test_strips_srcintf_dstintf(self):
        from nautobot_ssot_fortinet.utils.fortios import strip_pull_annotations

        assert strip_pull_annotations("Allow web [srcintf=lan dstintf=wan1]") == "Allow web"

    def test_strips_extintf(self):
        from nautobot_ssot_fortinet.utils.fortios import strip_pull_annotations

        assert strip_pull_annotations("VIP test [extintf=wan1]") == "VIP test"

    def test_strips_portforward(self):
        from nautobot_ssot_fortinet.utils.fortios import strip_pull_annotations

        assert strip_pull_annotations("[portforward TCP 80 -> 8080]") == ""

    def test_strips_multiple_annotations(self):
        from nautobot_ssot_fortinet.utils.fortios import strip_pull_annotations

        assert strip_pull_annotations("v [extintf=wan1] [portforward TCP 80 -> 8080]") == "v"

    def test_preserves_operator_brackets(self):
        from nautobot_ssot_fortinet.utils.fortios import strip_pull_annotations

        # Operator-added brackets like [CHANGE-1234] should survive
        assert strip_pull_annotations("[CHANGE-1234] Allow web [srcintf=lan dstintf=wan1]") == "[CHANGE-1234] Allow web"

    def test_passthrough_when_no_annotations(self):
        from nautobot_ssot_fortinet.utils.fortios import strip_pull_annotations

        assert strip_pull_annotations("just a comment") == "just a comment"

    def test_empty_string(self):
        from nautobot_ssot_fortinet.utils.fortios import strip_pull_annotations

        assert strip_pull_annotations("") == ""

    def test_idempotent(self):
        """Stripping twice should produce the same result as stripping once."""
        from nautobot_ssot_fortinet.utils.fortios import strip_pull_annotations

        once = strip_pull_annotations("Allow web [srcintf=lan dstintf=wan1]")
        twice = strip_pull_annotations(once)
        assert once == twice
