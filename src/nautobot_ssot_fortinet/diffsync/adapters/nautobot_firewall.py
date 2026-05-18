"""Nautobot-side DiffSync adapter for firewall objects.

``load()`` reads from ``nautobot-firewall-models`` ORM, scoped to a single
FortiGate by name-prefix match. The mangling convention
``<hostname>__<vdom>__<original>`` means we can scope without needing any
extra schema (no CustomFields, no Tags).

**Hostname constraint:** the ``hostname`` argument must not contain the
double-underscore separator. FortiGate device names with double underscores
are extremely rare in practice, but the adapter does not check or escape —
if you pass one, scoping will over-match and the diff will look chaotic.
"""

from __future__ import annotations

from diffsync import Adapter

from nautobot_ssot_fortinet.diffsync.models.nautobot_firewall import (
    NautobotAddressObject,
    NautobotAddressObjectGroup,
    NautobotNATPolicy,
    NautobotNATPolicyRule,
    NautobotPolicy,
    NautobotPolicyRule,
    NautobotServiceObject,
    NautobotServiceObjectGroup,
)
from nautobot_ssot_fortinet.utils.fortios import NAME_MANGLE_SEP, parse_intf_annotation


class NautobotFirewallAdapter(Adapter):
    """Read firewall-models ORM, scoped by FortiGate hostname + VDOM."""

    address_object = NautobotAddressObject
    address_object_group = NautobotAddressObjectGroup
    service_object = NautobotServiceObject
    service_object_group = NautobotServiceObjectGroup
    policy = NautobotPolicy
    policy_rule = NautobotPolicyRule
    nat_policy = NautobotNATPolicy
    nat_policy_rule = NautobotNATPolicyRule

    top_level = (
        "address_object",
        "address_object_group",
        "service_object",
        "service_object_group",
        "policy",
        "policy_rule",
        "nat_policy",
        "nat_policy_rule",
    )

    def __init__(
        self,
        *args,
        hostname: str,
        vdom: str = "root",
        job=None,
        sync=None,
        **kwargs,
    ) -> None:
        """Create the adapter.

        Args:
            hostname: FortiGate hostname; used to derive the name prefix
                that scopes the ORM queries to this device's records.
            vdom: VDOM scope; defaults to 'root'.
            job, sync: Standard SSoT plumbing.

        """
        super().__init__(*args, **kwargs)
        self.job = job
        self.sync = sync
        self.hostname = hostname
        self.vdom = vdom
        self.name_prefix = f"{hostname}{NAME_MANGLE_SEP}{vdom}{NAME_MANGLE_SEP}"

    def load(self) -> None:
        """Pull this FortiGate's records out of Nautobot in dependency order."""
        # ORM imports here, not at module top, so unit tests can import
        # the module against MagicMock-stubbed Django.
        from nautobot_firewall_models.models import (
            AddressObject as ORMAddressObject,
        )
        from nautobot_firewall_models.models import (
            AddressObjectGroup as ORMAddressObjectGroup,
        )
        from nautobot_firewall_models.models import (
            NATPolicy as ORMNATPolicy,
        )
        from nautobot_firewall_models.models import (
            NATPolicyRule as ORMNATPolicyRule,
        )
        from nautobot_firewall_models.models import (
            Policy as ORMPolicy,
        )
        from nautobot_firewall_models.models import (
            PolicyRule as ORMPolicyRule,
        )
        from nautobot_firewall_models.models import (
            ServiceObject as ORMServiceObject,
        )
        from nautobot_firewall_models.models import (
            ServiceObjectGroup as ORMServiceObjectGroup,
        )

        self._load_addresses(ORMAddressObject)
        self._load_address_groups(ORMAddressObjectGroup)
        self._load_services(ORMServiceObject, ORMServiceObjectGroup)
        self._load_service_groups(ORMServiceObjectGroup)
        self._load_policies(ORMPolicy, ORMPolicyRule)
        self._load_nat_policies(ORMNATPolicy, ORMNATPolicyRule)

    def _load_addresses(self, model) -> None:
        for orm_obj in model.objects.filter(name__startswith=self.name_prefix):
            address_type, value = _orm_address_value(orm_obj)
            if address_type is None:
                continue
            original_name = _strip_prefix(orm_obj.name, self.name_prefix)
            description = _strip_original_name_prefix(orm_obj.description or "", original_name)
            self.add(
                self.address_object(
                    name=orm_obj.name,
                    address_type=address_type,
                    value=value,
                    original_name=original_name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=description,
                )
            )

    def _load_address_groups(self, model) -> None:
        for orm_obj in model.objects.filter(name__startswith=self.name_prefix):
            members = sorted(m.name for m in orm_obj.address_objects.all() if m.name.startswith(self.name_prefix))
            original_name = _strip_prefix(orm_obj.name, self.name_prefix)
            description = _strip_original_name_prefix(orm_obj.description or "", original_name)
            self.add(
                self.address_object_group(
                    name=orm_obj.name,
                    members=members,
                    original_name=original_name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=description,
                )
            )

    def _load_services(self, svc_model, grp_model) -> None:
        # ServiceObjects have a composite NK (ip_protocol, port, name) and
        # no per-FortiGate scope at the schema level. They form a SHARED
        # POOL across all integrations — "HTTP TCP/80" defined by us and
        # by a hand-administered record collapse into the same row.
        #
        # Implication: we load ALL services unscoped. In additive-only
        # mode (the default) this is correct — we never delete services
        # we didn't create. In delete-mode (opt-in) the operator must
        # accept that "missing on this FortiGate" → "delete from Nautobot",
        # which may remove a service used by another integration. The
        # docstring on the Job documents this.
        for orm_obj in svc_model.objects.all():
            self.add(
                self.service_object(
                    name=orm_obj.name,
                    ip_protocol=orm_obj.ip_protocol,
                    port=orm_obj.port or "",
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=orm_obj.description or "",
                )
            )

    def _load_service_groups(self, model) -> None:
        for orm_obj in model.objects.filter(name__startswith=self.name_prefix):
            # Sort for stable diff (M2M is unordered).
            members = sorted((m.ip_protocol, m.port or "", m.name) for m in orm_obj.service_objects.all())
            original_name = _strip_prefix(orm_obj.name, self.name_prefix)
            description = _strip_original_name_prefix(orm_obj.description or "", original_name)
            self.add(
                self.service_object_group(
                    name=orm_obj.name,
                    members=members,
                    original_name=original_name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=description,
                )
            )

    def _load_policies(self, policy_model, rule_model) -> None:
        """Load the singleton Policy + all PolicyRules attached to it."""
        for orm_policy in policy_model.objects.filter(name__startswith=self.name_prefix):
            self.add(
                self.policy(
                    name=orm_policy.name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=orm_policy.description or "",
                )
            )
            # Iterate the M2M to find this Policy's rules. PolicyRule names
            # are not unique globally — scope to those linked to OUR Policy.
            for orm_rule in orm_policy.policy_rules.all():
                # Build the sorted member lists for stable diff against
                # the source-side adapter.
                src_addrs = sorted(o.name for o in orm_rule.source_addresses.all())
                src_grps = sorted(o.name for o in orm_rule.source_address_groups.all())
                dst_addrs = sorted(o.name for o in orm_rule.destination_addresses.all())
                dst_grps = sorted(o.name for o in orm_rule.destination_address_groups.all())
                dst_svcs = sorted((s.ip_protocol, s.port or "", s.name) for s in orm_rule.destination_services.all())
                dst_svc_grps = sorted(o.name for o in orm_rule.destination_service_groups.all())

                # Recover original_name from request_id (we stored up to 64
                # chars of the FortiOS policy name there during create) and
                # strip the "<original_name>: " prefix that create() added.
                original_name = orm_rule.request_id or orm_rule.name
                description = _strip_original_name_prefix(orm_rule.description or "", original_name)

                # v2.1+: parse the [srcintf=...] / [dstintf=...] annotation
                # back out of description into structured attrs. The
                # description still carries the annotation for human
                # readability in the Nautobot UI.
                src_intfs = sorted(parse_intf_annotation(orm_rule.description or "", "srcintf"))
                dst_intfs = sorted(parse_intf_annotation(orm_rule.description or "", "dstintf"))

                self.add(
                    self.policy_rule(
                        name=orm_rule.name,
                        policy_name=orm_policy.name,
                        action=orm_rule.action,
                        log=orm_rule.log,
                        index=orm_rule.index or 0,
                        original_name=original_name,
                        source_addresses=src_addrs,
                        source_address_groups=src_grps,
                        destination_addresses=dst_addrs,
                        destination_address_groups=dst_grps,
                        destination_services=dst_svcs,
                        destination_service_groups=dst_svc_grps,
                        source_interfaces=src_intfs,
                        destination_interfaces=dst_intfs,
                        vdom=self.vdom,
                        hostname=self.hostname,
                        description=description,
                    )
                )

    def _load_nat_policies(self, nat_policy_model, nat_rule_model) -> None:
        """Load the singleton NATPolicy + all NATPolicyRules attached to it."""
        for orm_policy in nat_policy_model.objects.filter(name__startswith=self.name_prefix):
            self.add(
                self.nat_policy(
                    name=orm_policy.name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=orm_policy.description or "",
                )
            )
            for orm_rule in orm_policy.nat_policy_rules.all():
                orig_dst_addr_objs = list(orm_rule.original_destination_addresses.all())
                xlat_dst_addr_objs = list(orm_rule.translated_destination_addresses.all())
                orig_dst_addrs = sorted(o.name for o in orig_dst_addr_objs)
                xlat_dst_addrs = sorted(o.name for o in xlat_dst_addr_objs)
                orig_dst_svcs = sorted(
                    (s.ip_protocol, s.port or "", s.name) for s in orm_rule.original_destination_services.all()
                )
                xlat_dst_svcs = sorted(
                    (s.ip_protocol, s.port or "", s.name) for s in orm_rule.translated_destination_services.all()
                )
                original_name = orm_rule.request_id or orm_rule.name
                description = _strip_original_name_prefix(orm_rule.description or "", original_name)
                # v2.1+: parse [extintf=X] back out of description
                extintf_list = parse_intf_annotation(orm_rule.description or "", "extintf")
                external_interface = extintf_list[0] if extintf_list else ""
                # v2.6+: resolve the actual IP values for diff fingerprinting.
                # FortiOS VIPs store extip/mappedip literally, so a value
                # change on the synth address needs to show up at the rule
                # level — including these in the diff makes that happen.
                resolved_extip = _first_addr_value(orig_dst_addr_objs)
                resolved_mappedip = _first_addr_value(xlat_dst_addr_objs)
                self.add(
                    self.nat_policy_rule(
                        name=orm_rule.name,
                        nat_policy_name=orm_policy.name,
                        log=orm_rule.log,
                        index=orm_rule.index or 0,
                        original_name=original_name,
                        original_destination_addresses=orig_dst_addrs,
                        translated_destination_addresses=xlat_dst_addrs,
                        original_destination_services=orig_dst_svcs,
                        translated_destination_services=xlat_dst_svcs,
                        resolved_extip=resolved_extip,
                        resolved_mappedip=resolved_mappedip,
                        external_interface=external_interface,
                        vdom=self.vdom,
                        hostname=self.hostname,
                        description=description,
                    )
                )


def _orm_address_value(orm_obj) -> tuple[str | None, str]:
    """Recover (address_type, value) from a Nautobot AddressObject's FKs.

    AddressObject enforces exactly one of (prefix, fqdn, ip_range, ip_address)
    is set — so this returns the first one found.
    """
    if orm_obj.prefix_id:
        return "ipmask", str(orm_obj.prefix.prefix)
    if orm_obj.fqdn_id:
        return "fqdn", orm_obj.fqdn.name
    if orm_obj.ip_range_id:
        rng = orm_obj.ip_range
        return "iprange", f"{rng.start_address}-{rng.end_address}"
    if orm_obj.ip_address_id:
        addr = orm_obj.ip_address
        return "ipaddress", str(addr.host)
    return None, ""


def _first_addr_value(addr_objs: list) -> str:
    """Resolve the first AddressObject in a list to its bare IP value.

    Used to populate NATPolicyRule.resolved_extip / resolved_mappedip so
    the DiffSync diff reflects value changes, not just M2M name changes.
    Returns "" if the list is empty or the first object doesn't resolve
    to a single IP (e.g., FQDN or iprange).
    """
    if not addr_objs:
        return ""
    addr_type, value = _orm_address_value(addr_objs[0])
    # For ipmask /32 we strip the suffix so it matches the pull-side
    # normalization to ipaddress/bare-IP done in v2.5.
    if addr_type == "ipmask" and value.endswith("/32"):
        return value.rsplit("/", 1)[0]
    if addr_type in ("ipaddress", "iprange"):
        return value
    if addr_type == "ipmask":
        # Subnet ranges /N where N < 32 — not what VIP extip/mappedip
        # expect, but include the value so a change is still detected.
        return value
    return ""


def _strip_prefix(s: str, prefix: str) -> str:
    """Like str.removeprefix, but explicit so the intent is grep-able."""
    return s[len(prefix) :] if s.startswith(prefix) else s


def _strip_original_name_prefix(description: str, original_name: str) -> str:
    """Reverse the ``"<original>: <description>"`` description convention."""
    head = f"{original_name}: "
    if description.startswith(head):
        return description[len(head) :]
    if description == original_name:
        return ""
    return description
