"""DiffSync model classes for FortiGate firewall objects.

These are the vendor-neutral "wire format" shared between the FortiGate
adapter (Phase 1) and the Nautobot adapter (Phase 2). Identifiers use the
**mangled** name (``<hostname>__<vdom>__<original>``) on every object that
maps to a Nautobot model with ``unique=True`` on ``name``. ``ServiceObject``
is the exception — it has a composite natural key in firewall-models, so
its identifier reflects that and its name is NOT mangled.

No CRUD methods are defined here — those land on subclasses inside each
adapter. Phase 1's FortiGate adapter only reads (load()), so it doesn't
need them; Phase 2's Nautobot adapter will subclass these and add
create/update/delete that write to the Django ORM.
"""

from __future__ import annotations

from diffsync import DiffSyncModel


class AddressObject(DiffSyncModel):
    """A FortiGate ``firewall/address`` object.

    Identifier (``name``) is the mangled form. ``address_type`` is the
    discriminator that tells the Nautobot adapter which of the 4 nullable
    FKs to populate on the Nautobot-side AddressObject:

    - ``ipmask``    → ``ipam.Prefix``    (``value`` is a CIDR string)
    - ``fqdn``      → firewall ``FQDN``  (``value`` is the FQDN)
    - ``iprange``   → firewall ``IPRange`` (``value`` is ``"start-end"``)
    - ``ipaddress`` → ``ipam.IPAddress`` (``value`` is a single IP)

    ``original_name`` is the un-mangled FortiOS name, preserved in the
    Nautobot ``description`` field for human readability.
    """

    _modelname = "address_object"
    _identifiers = ("name",)
    _attributes = (
        "address_type",
        "value",
        "original_name",
        "vdom",
        "hostname",
        "description",
    )

    name: str
    address_type: str
    value: str
    original_name: str
    vdom: str
    hostname: str
    description: str = ""


class AddressObjectGroup(DiffSyncModel):
    """A FortiGate ``firewall/addrgrp`` object.

    Members are stored as the mangled names of AddressObjects already
    loaded into the same adapter. Adapter load() ordering MUST resolve
    addresses before groups so the names are present.
    """

    _modelname = "address_object_group"
    _identifiers = ("name",)
    _attributes = ("members", "original_name", "vdom", "hostname", "description")

    name: str
    members: list[str]
    original_name: str
    vdom: str
    hostname: str
    description: str = ""


class ServiceObject(DiffSyncModel):
    """A FortiGate ``firewall.service/custom`` object.

    Identifier is composite ``(ip_protocol, port, name)`` to match
    ``nautobot-firewall-models.ServiceObject.natural_key_field_names``.
    No name mangling — duplicate names across protocols/ports are
    allowed by the Nautobot model.

    ``port`` is the FortiOS portrange string verbatim (may be a single
    port ``"443"``, a range ``"8000-8099"``, or space-separated multiples
    ``"80 443"``). Empty for protocols without port concept (ICMP, IP).
    """

    _modelname = "service_object"
    _identifiers = ("ip_protocol", "port", "name")
    _attributes = ("vdom", "hostname", "description")

    name: str
    ip_protocol: str
    port: str
    vdom: str
    hostname: str
    description: str = ""


class ServiceObjectGroup(DiffSyncModel):
    """A FortiGate ``firewall.service/group`` object.

    Members are stored as ServiceObject natural-key tuples
    ``(ip_protocol, port, name)`` to match the composite identifier on
    the ServiceObject model.
    """

    _modelname = "service_object_group"
    _identifiers = ("name",)
    _attributes = ("members", "original_name", "vdom", "hostname", "description")

    name: str
    members: list[tuple[str, str, str]]
    original_name: str
    vdom: str
    hostname: str
    description: str = ""


class Policy(DiffSyncModel):
    """A firewall-models ``Policy`` — one per (FortiGate, VDOM).

    A Policy is just a named container for PolicyRules. The actual rule
    contents live on PolicyRule. We sync one Policy per FortiGate-VDOM,
    named ``<hostname>__<vdom>__policy``.
    """

    _modelname = "policy"
    _identifiers = ("name",)
    _attributes = ("vdom", "hostname", "description")

    name: str
    vdom: str
    hostname: str
    description: str = ""


class PolicyRule(DiffSyncModel):
    """A single FortiGate ``firewall/policy`` entry → firewall-models PolicyRule.

    Identifier is mangled as ``<hostname>__<vdom>__rule_<policyid>`` since
    FortiOS policy names can be empty or duplicate, but ``policyid`` is
    always unique within a VDOM.

    ``policy_name`` is the **mangled** parent Policy name — the Nautobot
    adapter uses it to look up the parent and add the rule to its M2M.

    The 12 source/destination M2M fields are stored as sorted lists for
    stable diff (Django M2M is unordered):

    - leaf lists (``*_addresses``, ``*_address_groups``, ``*_service_groups``):
      sorted list of mangled names
    - composite-key list (``destination_services``, ``source_services``):
      sorted list of ``(ip_protocol, port, name)`` tuples
    """

    _modelname = "policy_rule"
    _identifiers = ("name",)
    _attributes = (
        "policy_name",
        "action",
        "log",
        "index",
        "original_name",
        "source_addresses",
        "source_address_groups",
        "destination_addresses",
        "destination_address_groups",
        "destination_services",
        "destination_service_groups",
        "source_interfaces",
        "destination_interfaces",
        "vdom",
        "hostname",
        "description",
    )

    name: str
    policy_name: str
    action: str
    log: bool
    index: int
    original_name: str
    source_addresses: list[str]
    source_address_groups: list[str]
    destination_addresses: list[str]
    destination_address_groups: list[str]
    destination_services: list[tuple[str, str, str]]
    destination_service_groups: list[str]
    # FortiOS srcintf/dstintf can be either interface names ("lan", "wan1")
    # or zone names — both are looked up the same way at policy-evaluation
    # time. We store them as raw string lists so push can emit the FortiOS
    # ``[{"name": "..."}]`` member format directly.
    source_interfaces: list[str]
    destination_interfaces: list[str]
    vdom: str
    hostname: str
    description: str = ""


class NATPolicy(DiffSyncModel):
    """A firewall-models ``NATPolicy`` — one per (FortiGate, VDOM).

    Singleton container — same shape as ``Policy``. Mangled name:
    ``<hostname>__<vdom>__nat_policy``.
    """

    _modelname = "nat_policy"
    _identifiers = ("name",)
    _attributes = ("vdom", "hostname", "description")

    name: str
    vdom: str
    hostname: str
    description: str = ""


class NATPolicyRule(DiffSyncModel):
    """A FortiGate ``firewall/vip`` → firewall-models NATPolicyRule (DNAT).

    Identifier: ``<hostname>__<vdom>__nat_rule_<vip-name>``. We only
    populate ``original_destination_*`` and ``translated_destination_*``
    fields — VIPs are DNAT only. Source NAT (FortiOS ``ippool``) is not
    in scope for Phase 5 v1.

    The ``original_destination_addresses`` and
    ``translated_destination_addresses`` lists contain MANGLED names of
    AddressObjects synthesized by the adapter from the VIP's ``extip``
    and ``mappedip`` fields — those addresses aren't separately defined
    in FortiOS, so we manufacture them. Their names follow the convention
    ``<hostname>__<vdom>__vip_<vipname>_ext`` / ``_mapped``.

    When ``portforward=enable``, the rule also populates
    ``original_destination_services`` (the external port) and
    ``translated_destination_services`` (the internal port) with NK
    tuples for synthesized ServiceObjects.
    """

    _modelname = "nat_policy_rule"
    _identifiers = ("name",)
    _attributes = (
        "nat_policy_name",
        "log",
        "index",
        "original_name",
        "original_destination_addresses",
        "translated_destination_addresses",
        "original_destination_services",
        "translated_destination_services",
        # v2.6+: resolved-IP fingerprints. The *_addresses fields above
        # carry NAMES of AddressObjects; these carry the actual IP VALUES
        # those names resolve to. FortiOS VIPs store extip/mappedip
        # literally (not by reference), so a value change without a name
        # change still needs to propagate. Including resolved values in
        # the DiffSync diff means "edit the IP on the synth address +
        # push" updates the VIP — no operator workflow constraint.
        # NOT leading-underscore: Pydantic v2 treats _name as private and
        # excludes from diff comparison.
        "resolved_extip",
        "resolved_mappedip",
        "external_interface",
        "vdom",
        "hostname",
        "description",
    )

    name: str
    nat_policy_name: str
    log: bool
    index: int
    original_name: str
    original_destination_addresses: list[str]
    translated_destination_addresses: list[str]
    original_destination_services: list[tuple[str, str, str]]
    translated_destination_services: list[tuple[str, str, str]]
    resolved_extip: str = ""
    resolved_mappedip: str = ""
    # FortiOS VIP `extintf` — the interface on which the external IP is
    # reachable. Empty string for "any" (FortiOS default).
    external_interface: str
    vdom: str
    hostname: str
    description: str = ""
