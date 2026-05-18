"""Helpers for translating FortiOS data shapes into Nautobot-friendly values.

Functions here are pure (no I/O, no DB) so they can be unit-tested in
isolation from any Nautobot fixture.
"""

from __future__ import annotations

import ipaddress
from typing import Any

# Sentinel separator for name mangling — picked to be (a) grep-friendly,
# (b) extremely unlikely in real FortiGate object names, (c) URL-safe so
# Nautobot's slug machinery doesn't choke.
NAME_MANGLE_SEP = "__"


class FortiOSAPIError(RuntimeError):
    """Raised when a FortiOS REST call returns a non-success HTTP status.

    The exception preserves the parsed FortiOS response body (``status``,
    ``error``, ``cli_error``, ``http_status``) and the originating label so
    log messages and stack traces include enough context to diagnose the
    failed payload — pre-v2.4 silent 500s masked a wtp-profile create bug
    for 3 releases. Catching this in model code lets us log the FortiOS
    diagnostic AND skip the DiffSync store update so we don't claim
    success on a failed write.
    """


def check_fortios_response(resp: Any, label: str) -> Any:
    """Raise FortiOSAPIError if ``resp.status_code != 200``.

    fortigate-api's Connector returns the raw ``requests.Response`` on
    create/update calls but doesn't itself check status. FortiOS uses 500
    + ``{"status":"error","error":-1}`` for many user-fixable mistakes
    (wrong field shape, validation failures, hardware constraints). We
    surface those in the exception message so the caller sees what
    happened on the device.

    Returns ``resp`` unchanged on success so this can be inlined at the
    call site: ``check_fortios_response(api.create(data=x), label="...")``.
    """
    status_code = getattr(resp, "status_code", None)
    if status_code == 200:
        return resp
    body_summary = ""
    try:
        body = resp.json()
        body_summary = f" status={body.get('status')!r} error={body.get('error')!r} cli_error={body.get('cli_error')!r}"
    except Exception:
        body_summary = f" body={getattr(resp, 'text', '')[:200]!r}"
    raise FortiOSAPIError(f"FortiOS rejected {label}: http={status_code}{body_summary}")


# IANA protocol number → ``nautobot_firewall_models.choices.IP_PROTOCOL_CHOICES``
# value. Used when FortiOS reports a service as ``protocol: "IP"`` with a
# ``protocol-number`` — firewall-models doesn't accept the literal "IP",
# it wants the named protocol from its enum (e.g. 89 → "OSPFIGP", not "OSPF").
# Names match what firewall-models exposes; verified against
# ``nautobot_firewall_models.choices.IP_PROTOCOL_CHOICES`` (144 entries).
# Only the protocols most likely to appear in FortiGate service custom
# defs are mapped — unmapped numbers cause the FortiGate adapter to skip
# the service with a warning rather than crash on a ValidationError.
IP_PROTOCOL_NUMBER_TO_NAME: dict[int, str] = {
    1: "ICMP",
    2: "IGMP",
    4: "IPv4",  # IP-in-IP encapsulation
    6: "TCP",
    8: "EGP",
    17: "UDP",
    41: "IPv6",
    47: "GRE",
    50: "ESP",
    51: "AH",
    88: "EIGRP",
    89: "OSPFIGP",
    103: "PIM",
    112: "VRRP",
    115: "L2TP",
    132: "SCTP",
}


def mangle_name(hostname: str, vdom: str, original_name: str) -> str:
    """Build the globally-unique Nautobot name for a per-FortiGate object.

    ``nautobot-firewall-models`` enforces ``unique=True`` on the ``name`` field
    of most objects (AddressObject, AddressObjectGroup, FQDN, Zone, Policy,
    NATPolicy, ServiceObjectGroup). Two FortiGates — or two VDOMs on one
    FortiGate — can legitimately each have an AddressObject named
    ``WEB_SERVERS``, which would violate uniqueness if synced verbatim.

    The convention used throughout this app: prefix the original name with
    ``<hostname>__<vdom>__``. The original name is preserved in the object's
    ``description`` field for human readability.

    >>> mangle_name("fgt-edge1", "root", "WEB_SERVERS")
    'fgt-edge1__root__WEB_SERVERS'

    Note: ``ServiceObject`` is the **exception** — it has a composite natural
    key ``(ip_protocol, port, name)`` and no ``unique=True`` on ``name``, so
    its names are NOT mangled.
    """
    return f"{hostname}{NAME_MANGLE_SEP}{vdom}{NAME_MANGLE_SEP}{original_name}"


def fortios_subnet_to_cidr(subnet_str: str) -> str:
    """Convert FortiOS dotted-mask notation to CIDR.

    FortiOS represents IPv4 networks in ``firewall/address`` as
    ``"10.0.0.0 255.255.255.0"`` (a single string, two space-separated parts).
    Nautobot's ``ipam.Prefix.prefix`` field expects CIDR notation.

    >>> fortios_subnet_to_cidr("10.0.0.0 255.255.255.0")
    '10.0.0.0/24'
    >>> fortios_subnet_to_cidr("0.0.0.0 0.0.0.0")
    '0.0.0.0/0'
    >>> fortios_subnet_to_cidr("192.168.1.1 255.255.255.255")
    '192.168.1.1/32'

    For IPv6 (which FortiOS exposes under ``firewall/address6`` — not in
    scope for Phase 1) the input would already be CIDR; this helper is
    IPv4-only and will raise on IPv6 input.

    Raises:
        ValueError: if the input is not parseable as IPv4 dotted-mask.

    """
    parts = subnet_str.strip().split()
    if len(parts) != 2:
        raise ValueError(f"expected 'address mask' format with one space, got: {subnet_str!r}")
    address, mask = parts
    network = ipaddress.IPv4Network(f"{address}/{mask}", strict=False)
    return str(network)


def fortios_service_ports(svc: dict) -> tuple[str | None, str]:
    """Pick the right port-range field for a FortiOS service object.

    FortiOS ``firewall.service/custom`` carries protocol-specific fields:
    ``tcp-portrange``, ``udp-portrange``, ``sctp-portrange``, ``icmptype``,
    ``protocol-number``. Only one is populated based on ``protocol``.

    Returns ``(ip_protocol, port_range)`` where:

    - ``ip_protocol`` is the firewall-models choice string ('TCP', 'UDP',
      'ICMP', 'IPv6-ICMP', 'OSPFIGP', 'GRE', etc.) — for ``protocol: "IP"``
      services this translates the ``protocol-number`` to its IANA name via
      :data:`IP_PROTOCOL_NUMBER_TO_NAME`. Returns ``None`` when the
      protocol number isn't in the mapping; callers should skip that
      record with a warning.
    - ``port_range`` is the FortiOS port-range string **with spaces
      converted to commas**. FortiOS uses space-separated multi-port
      values (``"88 464"`` for Kerberos) but
      ``nautobot_firewall_models.validators.validate_port`` splits on
      commas only — and emits a broken error message on mismatched input
      (``KeyError: 'i'`` from a malformed ``%(i)s`` template). Pre-comma
      output avoids the validator's bug.

    The 'TCP/UDP/SCTP' value of the ``protocol`` field is a FortiOS
    convention meaning "any of these" — the populated subfield disambiguates.
    """
    proto = svc.get("protocol", "")

    if proto in {"TCP/UDP/SCTP", "TCP", "UDP", "SCTP"}:
        # Whichever sub-portrange is populated wins; if multiple are
        # populated, FortiOS treats the service as 'any of these' and we
        # pick TCP first by convention.
        for sub in ("tcp-portrange", "udp-portrange", "sctp-portrange"):
            if svc.get(sub):
                # Two consecutive normalizations:
                # 1. strip the ``:src-range`` qualifier (RLOGIN/RSH)
                # 2. convert spaces to commas (KERBEROS-style multi-port)
                raw = _normalize_fortios_dst_src_port(str(svc[sub]))
                return _proto_for_sub(sub), _normalize_port_separators(raw)
        return "TCP", ""

    if proto == "ICMP":
        icmptype = svc.get("icmptype")
        return "ICMP", str(icmptype) if icmptype is not None else ""

    if proto == "ICMP6":
        # FortiOS spells it "ICMP6"; firewall-models uses IANA "IPv6-ICMP".
        icmptype = svc.get("icmptype")
        return "IPv6-ICMP", str(icmptype) if icmptype is not None else ""

    if proto == "IP":
        protnum = svc.get("protocol-number")
        if protnum is None:
            return None, ""
        name = IP_PROTOCOL_NUMBER_TO_NAME.get(int(protnum))
        if name is None:
            return None, ""  # unmapped; caller skips
        return name, ""

    if proto == "ALL":
        # FortiOS ``protocol: ALL`` means "match any IP protocol" — e.g.
        # the built-in ``webproxy`` service. firewall-models has no
        # equivalent choice; skip with a None signal.
        return None, ""

    return proto or "TCP", ""


def parse_intf_annotation(description: str, key: str) -> list[str]:
    """Extract an ``[<key>=v1,v2,...]`` annotation back into a list of names.

    The pull-side adapter emits descriptions with annotations like
    ``[srcintf=lan dstintf=wan1]`` so humans see interface info in the
    Nautobot UI. This helper reverses that for the push side — the
    Nautobot adapter calls it during ``_load_policies()`` to populate
    the structured ``source_interfaces`` / ``destination_interfaces`` /
    ``external_interface`` DiffSync attrs.

    >>> parse_intf_annotation("Internal users [srcintf=lan dstintf=wan1]", "srcintf")
    ['lan']
    >>> parse_intf_annotation("Internal users [srcintf=lan,vlan10 dstintf=wan1]", "srcintf")
    ['lan', 'vlan10']
    >>> parse_intf_annotation("Internal users [extintf=wan1]", "extintf")
    ['wan1']
    >>> parse_intf_annotation("just a comment", "srcintf")
    []
    >>> parse_intf_annotation("", "srcintf")
    []
    """
    import re

    # The key may be preceded by either `[` (first key in annotation) OR
    # whitespace (second/third key in annotation). Both forms appear in
    # the pull-side output: `[srcintf=lan dstintf=wan1]`.
    match = re.search(rf"(?:[\[\s]){re.escape(key)}=([^\]\s]+)(?:\s|\])", description)
    if not match:
        return []
    return [part for part in match.group(1).split(",") if part]


def strip_pull_annotations(comment: str) -> str:
    """Remove the machine-generated annotations that the pull adapter adds.

    The pull adapter appends annotations like ``[srcintf=lan dstintf=wan1]``
    and ``[extintf=wan1]`` and ``[portforward TCP 80 -> 8080]`` to the
    FortiOS-side comment when building the description. When we push the
    description BACK to FortiOS as a comment, we have to strip those
    annotations first — otherwise the next pull sees the annotation in
    the FortiOS comment AND re-adds it, producing duplicated
    ``[extintf=wan1] [extintf=wan1]`` on every round-trip.

    Only strips the exact shapes the pull adapter produces. Operator-added
    bracket content (``[INTERNAL]``, ``[CHANGE-1234]``, etc.) is preserved
    because the annotation keys (``srcintf``, ``dstintf``, ``extintf``,
    ``portforward``) are extremely unlikely to appear in human comments.

    >>> strip_pull_annotations("Allow web [srcintf=lan dstintf=wan1]")
    'Allow web'
    >>> strip_pull_annotations("VIP for app [extintf=wan1]")
    'VIP for app'
    >>> strip_pull_annotations("Port-fwd [extintf=wan1] [portforward TCP 80 -> 8080]")
    'Port-fwd'
    >>> strip_pull_annotations("[CHANGE-1234] Allow web [srcintf=lan dstintf=wan1]")
    '[CHANGE-1234] Allow web'
    >>> strip_pull_annotations("just a comment")
    'just a comment'
    """
    import re

    patterns = [
        # [srcintf=... dstintf=...]
        r"\s*\[srcintf=[^\]]*dstintf=[^\]]*\]",
        # [extintf=...]
        r"\s*\[extintf=[^\]]+\]",
        # [portforward PROTO PORT -> PORT]
        r"\s*\[portforward\s+[^\]]+\]",
    ]
    out = comment
    for pat in patterns:
        out = re.sub(pat, "", out)
    return out.strip()


def denormalize_port_separators(comma_form: str) -> str:
    """Inverse of :func:`_normalize_port_separators`: comma → space.

    Used on the push side — Nautobot stores ``"88,464"`` (comma); FortiOS
    expects ``"88 464"`` (space) in ``tcp-portrange`` and friends.

    >>> denormalize_port_separators("88,464")
    '88 464'
    >>> denormalize_port_separators("80")
    '80'
    >>> denormalize_port_separators("8000-8099")
    '8000-8099'
    """
    return " ".join(comma_form.split(","))


# Inverse of :data:`IP_PROTOCOL_NUMBER_TO_NAME` for push direction. Multiple
# IANA names could share a number in edge cases, so we accept the first.
IP_PROTOCOL_NAME_TO_NUMBER: dict[str, int] = {name: num for num, name in IP_PROTOCOL_NUMBER_TO_NAME.items()}


def build_fortios_service_payload(name: str, ip_protocol: str, port: str, description: str = "") -> dict | None:
    """Build a FortiOS ``firewall.service/custom`` payload from our DiffSync attrs.

    Inverse of :func:`fortios_service_ports`. Returns ``None`` for
    ip_protocols that have no clean FortiOS round-trip — caller should
    skip with a warning.

    Mappings:
        ``TCP``        → ``protocol=TCP/UDP/SCTP, tcp-portrange=...``
        ``UDP``        → ``protocol=TCP/UDP/SCTP, udp-portrange=...``
        ``SCTP``       → ``protocol=TCP/UDP/SCTP, sctp-portrange=...``
        ``ICMP``       → ``protocol=ICMP, icmptype=<port-as-int>``
        ``IPv6-ICMP``  → ``protocol=ICMP6, icmptype=<port-as-int>``
        any name in :data:`IP_PROTOCOL_NAME_TO_NUMBER`
                       → ``protocol=IP, protocol-number=<num>``

    >>> build_fortios_service_payload("HTTPS", "TCP", "443")
    {'name': 'HTTPS', 'protocol': 'TCP/UDP/SCTP', 'tcp-portrange': '443', 'comment': ''}
    >>> build_fortios_service_payload("KERBEROS", "TCP", "88,464")
    {'name': 'KERBEROS', 'protocol': 'TCP/UDP/SCTP', 'tcp-portrange': '88 464', 'comment': ''}
    >>> build_fortios_service_payload("OSPF", "OSPFIGP", "")
    {'name': 'OSPF', 'protocol': 'IP', 'protocol-number': 89, 'comment': ''}
    """
    payload: dict = {"name": name, "comment": description[:255]}

    if ip_protocol in {"TCP", "UDP", "SCTP"}:
        payload["protocol"] = "TCP/UDP/SCTP"
        payload[f"{ip_protocol.lower()}-portrange"] = denormalize_port_separators(port)
        return payload

    if ip_protocol == "ICMP":
        payload["protocol"] = "ICMP"
        if port:
            try:
                payload["icmptype"] = int(port)
            except ValueError:
                pass
        return payload

    if ip_protocol == "IPv6-ICMP":
        payload["protocol"] = "ICMP6"
        if port:
            try:
                payload["icmptype"] = int(port)
            except ValueError:
                pass
        return payload

    if ip_protocol in IP_PROTOCOL_NAME_TO_NUMBER:
        payload["protocol"] = "IP"
        payload["protocol-number"] = IP_PROTOCOL_NAME_TO_NUMBER[ip_protocol]
        return payload

    return None  # unknown protocol — caller skips


def _normalize_fortios_dst_src_port(port_value: str) -> str:
    """Strip FortiOS source-port qualifier from a dst-port string.

    FortiOS lets a service specify both a destination port AND a range of
    **source** ports the connection must originate from, joined with ``:``.

    Examples:
        ``"513:512-1023"`` — dst 513, src must be in 512..1023 (RLOGIN)
        ``"514:512-1023"`` — dst 514, src must be in 512..1023 (RSH)

    Nautobot's ServiceObject has no source-port concept, so we drop the
    qualifier and keep only the destination side. This is a documented
    lossy mapping — operators relying on FortiOS source-port restrictions
    will see them disappear in Nautobot, but the rule is *semantically
    correct* for normal traffic where source ports are ephemeral.

    >>> _normalize_fortios_dst_src_port("513:512-1023")
    '513'
    >>> _normalize_fortios_dst_src_port("80")
    '80'
    >>> _normalize_fortios_dst_src_port("80 443")
    '80 443'

    """
    return port_value.split(":", 1)[0]


def _normalize_port_separators(port_value: str) -> str:
    """Convert FortiOS-style space-separated port lists to comma-separated.

    FortiOS: ``"88 464"`` (space) — for multi-port services like Kerberos.
    firewall-models: ``"88,464"`` (comma) — the validator splits on ",".

    >>> _normalize_port_separators("88 464")
    '88,464'
    >>> _normalize_port_separators("80")
    '80'
    >>> _normalize_port_separators("8000-8099")
    '8000-8099'
    >>> _normalize_port_separators("80 443  993")  # collapses runs of spaces
    '80,443,993'
    """
    return ",".join(port_value.split())


def _proto_for_sub(subkey: str) -> str:
    """tcp-portrange → TCP, etc."""
    return subkey.split("-", 1)[0].upper()


# FortiOS firewall policy ``action`` field → ``firewall-models`` ACTION_CHOICES
# value. FortiOS uses CamelCase-ish vendor terms; firewall-models normalizes
# to lowercase IETF-style. ``ipsec`` is a FortiOS-specific action that
# routes traffic into an IPsec tunnel — semantically "allow but with side
# effects". We map it to "allow" and note the original in the description.
FORTIOS_ACTION_MAP: dict[str, str] = {
    "accept": "allow",
    "permit": "allow",
    "allow": "allow",
    "deny": "deny",
    "drop": "drop",
    "ipsec": "allow",
}


def fortios_action(raw_action: str) -> tuple[str, str | None]:
    """Map a FortiOS action to a firewall-models action.

    Returns ``(mapped_action, note)`` where ``note`` is a human-readable
    annotation to splice into the rule's description if the mapping was
    lossy (e.g. ``ipsec`` → ``allow`` loses the tunnel-routing intent).
    Returns ``("allow", None)`` for the common ``accept`` case where no
    annotation is needed.

    >>> fortios_action("accept")
    ('allow', None)
    >>> fortios_action("ipsec")
    ('allow', 'FortiOS action=ipsec (tunnel-routed) flattened to allow')
    >>> fortios_action("deny")
    ('deny', None)
    """
    mapped = FORTIOS_ACTION_MAP.get(raw_action.lower(), "deny")
    if raw_action.lower() == "ipsec":
        return mapped, "FortiOS action=ipsec (tunnel-routed) flattened to allow"
    if raw_action.lower() not in FORTIOS_ACTION_MAP:
        return mapped, f"Unknown FortiOS action={raw_action!r} fallback to deny"
    return mapped, None


# FortiOS VAP ``security`` field value → Nautobot
# ``WirelessNetworkAuthenticationChoices`` value. Keys are kept lowercase;
# the lookup function lowercases the input. FortiOS uses hyphen-separated
# vendor terms; Nautobot uses display-style camel case.
FORTIOS_VAP_SECURITY_MAP: dict[str, str] = {
    "open": "Open",
    "captive-portal": "Open",  # captive portal layered on open
    "wep64": "Open",  # legacy — treat as "Open" since Nautobot has no WEP choice
    "wep128": "Open",
    "wpa-personal": "WPA2 Personal",  # WPA1+WPA2 mixed-mode
    "wpa-personal+pmf": "WPA2 Personal",
    "wpa2-only-personal": "WPA2 Personal",
    "wpa-enterprise": "WPA2 Enterprise",
    "wpa2-only-enterprise": "WPA2 Enterprise",
    "wpa3-sae": "WPA3 SAE",
    "wpa3-sae-transition": "WPA3 SAE",
    "wpa3-only-personal": "WPA3 Personal",  # PSK-style WPA3 (less common term)
    "wpa3-personal": "WPA3 Personal",
    "wpa3-enterprise": "WPA3 Enterprise",
    "wpa3-only-enterprise": "WPA3 Enterprise",
    "wpa3-enterprise-192": "WPA3 Enterprise 192Bit",
    "wpa3-only-enterprise-192": "WPA3 Enterprise 192Bit",
    "owe": "Enhanced Open",
    "osen": "Enhanced Open",  # OWE+OSEN family
}


# FortiOS ``wtp-profile.platform-mode`` (e.g. ``FortiAP-tunnel-mode`` or
# ``FortiAP-local-mode``) → Nautobot ``WirelessNetworkModeChoices``. Note
# that Nautobot puts ``mode`` on the WirelessNetwork; FortiOS puts it on
# the WTP-profile. When a single WirelessNetwork spans multiple
# WTP-profiles with conflicting modes, the adapter picks the most common
# value and logs a warning.
FORTIOS_PLATFORM_MODE_MAP: dict[str, str] = {
    "fortiap-tunnel-mode": "Central",  # backhauled to WLC
    "fortiap-local-mode": "Local (Flex)",  # local-switched
    "tunnel-mode": "Central",  # short form
    "local-mode": "Local (Flex)",
    "wpa-mesh-mode": "Mesh",
    "mesh": "Mesh",
    "bridge-mode": "Bridge",
}


def fortios_security_to_auth(security: str) -> tuple[str, str | None]:
    """Map FortiOS ``vap.security`` to a Nautobot authentication choice.

    Returns ``(auth_value, note)`` where ``note`` is a human-readable
    annotation if the mapping was lossy or fell back to a default.

    >>> fortios_security_to_auth("wpa2-only-personal")
    ('WPA2 Personal', None)
    >>> fortios_security_to_auth("wep128")
    ('Open', "FortiOS security=wep128 has no Nautobot equivalent — fell back to Open")
    >>> fortios_security_to_auth("totally-bogus")
    ('Open', "Unknown FortiOS security='totally-bogus' — fell back to Open")
    """
    key = (security or "").lower()
    if key in FORTIOS_VAP_SECURITY_MAP:
        mapped = FORTIOS_VAP_SECURITY_MAP[key]
        if key in {"wep64", "wep128", "captive-portal"}:
            return mapped, (f"FortiOS security={security} has no Nautobot equivalent — fell back to {mapped}")
        return mapped, None
    return "Open", f"Unknown FortiOS security={security!r} — fell back to Open"


def fortios_platform_mode_to_network_mode(platform_mode: str) -> str:
    """Map FortiOS ``wtp-profile.platform-mode`` to Nautobot WirelessNetwork mode.

    Falls back to ``"Central"`` for unknown modes. Falls back to
    ``"Central"`` rather than raising because FortiOS occasionally emits
    a platform name without our recognized prefix, and we'd rather sync
    with a sane default than fail the whole sync.
    """
    key = (platform_mode or "").lower()
    return FORTIOS_PLATFORM_MODE_MAP.get(key, "Central")


def fortios_band_to_frequency(band: str) -> str | None:
    """Map a FortiOS radio band string to Nautobot ``RadioProfileFrequencyChoices`` value.

    FortiOS radio bands come as bespoke 802.11-variant strings:
    ``802.11ax-5G``, ``802.11n,g-only``, ``802.11ax``, etc. We pattern-match
    on the ``-N..G`` suffix or recognized band qualifiers.

    Returns the Nautobot value (``"2.4GHz"``, ``"5GHz"``, ``"6GHz"``) or
    ``None`` if the band can't be classified — caller should skip the
    radio with a warning rather than crash.

    >>> fortios_band_to_frequency("802.11ax-5G")
    '5GHz'
    >>> fortios_band_to_frequency("802.11n,g-only")
    '2.4GHz'
    >>> fortios_band_to_frequency("802.11ax-6G")
    '6GHz'
    >>> fortios_band_to_frequency("802.11ac")
    '5GHz'
    >>> fortios_band_to_frequency("802.11n")
    '2.4GHz'
    >>> fortios_band_to_frequency("disabled")  # returns None — skip
    """
    if not band:
        return None
    b = band.lower()
    # Explicit -6G suffix wins regardless of substring overlap.
    if "-6g" in b or "6ghz" in b:
        return "6GHz"
    if "-5g" in b or "5ghz" in b or "ac" in b:
        return "5GHz"
    if "-2g" in b or "2.4" in b or any(t in b for t in ("802.11g", "802.11n", "n,g")):
        # 802.11n alone could be 2.4 OR 5; FortiOS defaults to 2.4 in that
        # case for legacy reasons. Anything explicitly 5G already matched.
        return "2.4GHz"
    if "ax" in b:  # 802.11ax without -NG suffix → tri-band capable; default 5G
        return "5GHz"
    return None


def split_policy_members(
    fortios_member_list: list[dict],
    leaf_names: set[str],
    group_names: set[str],
    mangler,
) -> tuple[list[str], list[str]]:
    """Split a FortiOS policy member list into (leaves, groups).

    FortiOS firewall policies use a single ``srcaddr``/``dstaddr`` field
    that can contain a mix of address objects and address groups; the API
    response doesn't distinguish them. We mangle each FortiOS-raw name and
    classify by lookup against the (already-mangled) sets of names the
    adapter loaded earlier.

    Args:
        fortios_member_list: ``[{"name": "WEB_SERVERS"}, ...]`` shape — names
            are FortiOS-raw (un-mangled).
        leaf_names: set of MANGLED leaf object names already in the store.
        group_names: set of MANGLED group object names already in the store.
        mangler: callable ``(raw_name) -> mangled_name``. Typically built
            with ``functools.partial(mangle_name, hostname, vdom)``.

    Returns:
        ``(sorted_leaves, sorted_groups)`` — both contain MANGLED names so
        they're ready to store as DiffSync attrs. Names not found in
        either set are silently dropped.

    """
    leaves: list[str] = []
    groups: list[str] = []
    for entry in fortios_member_list:
        n = entry.get("name")
        if not n:
            continue
        mn = mangler(n)
        if mn in leaf_names:
            leaves.append(mn)
        elif mn in group_names:
            groups.append(mn)
        # else: unknown name — caller may log a warning
    return sorted(leaves), sorted(groups)
