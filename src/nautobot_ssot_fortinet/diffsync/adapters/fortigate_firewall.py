"""FortiGate-side DiffSync adapter for firewall objects.

Loads addresses, address groups, services, and service groups from a
FortiGate REST API into the DiffSync store. Read-only — the FortiGate side
never accepts writes from this integration (v1 is one-way: FortiGate →
Nautobot).

Load order MUST be dependency-first: leaf objects before groups that
reference them, otherwise group member resolution finds nothing.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from diffsync import Adapter
from diffsync.exceptions import ObjectNotFound

from nautobot_ssot_fortinet.diffsync.models.firewall import (
    AddressObject,
    AddressObjectGroup,
    NATPolicy,
    NATPolicyRule,
    Policy,
    PolicyRule,
    ServiceObject,
    ServiceObjectGroup,
)
from nautobot_ssot_fortinet.utils.fortios import (
    fortios_action,
    fortios_service_ports,
    fortios_subnet_to_cidr,
    mangle_name,
    split_policy_members,
)

if TYPE_CHECKING:
    from fortigate_api import FortiGateAPI


class FortiGateFirewallAdapter(Adapter):
    """Read FortiGate firewall config into DiffSync."""

    address_object = AddressObject
    address_object_group = AddressObjectGroup
    service_object = ServiceObject
    service_object_group = ServiceObjectGroup
    policy = Policy
    policy_rule = PolicyRule
    nat_policy = NATPolicy
    nat_policy_rule = NATPolicyRule

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
        client: FortiGateAPI,
        hostname: str,
        vdom: str = "root",
        job=None,
        sync=None,
        **kwargs,
    ) -> None:
        """Create the adapter.

        Args:
            client: A fortigate-api ``FortiGateAPI`` instance. Tests pass
                a mock; production passes one built by ``clients.fortigate.build_client``.
            hostname: Logical name of this FortiGate (used as the first
                segment of the mangled name). Should match the
                ``ExternalIntegration.name`` for traceability.
            vdom: FortiOS Virtual Domain to load from. Defaults to 'root'
                (single-VDOM FortiGates always have a 'root' VDOM).
            job, sync: Standard SSoT plumbing passed through to DiffSync.

        """
        # diffsync.Adapter accepts only `name=`; the nautobot-ssot convention
        # of passing job/sync is layered on top (the Job uses them for log
        # routing). We store them on self so the adapter code can call
        # self.job.logger.warning(...) without crashing when job is None.
        super().__init__(*args, **kwargs)
        self.job = job
        self.sync = sync
        self.client = client
        self.hostname = hostname
        self.vdom = vdom

    def load(self) -> None:
        """Pull all object kinds in dependency order — leaves first, then groups, then policies, then NAT."""
        self._load_addresses()
        self._load_address_groups()
        self._load_services()
        self._load_service_groups()
        self._load_policies()
        # NAT VIPs come last because they synthesize new AddressObjects +
        # ServiceObjects on-the-fly that get added to the same store.
        self._load_nat_vips()

    def _load_addresses(self) -> None:
        for raw in self.client.cmdb.firewall.address.get():
            original_name = raw.get("name", "")
            if not original_name:
                continue
            address_type, value = _address_value(raw)
            if address_type is None:
                # Geography / wildcard / dynamic types not modeled in
                # firewall-models — skip with a job log entry if present.
                if self.job:
                    self.job.logger.warning(
                        f"Skipping address {original_name!r}: unsupported FortiOS type {raw.get('type')!r}"
                    )
                continue
            self.add(
                self.address_object(
                    name=mangle_name(self.hostname, self.vdom, original_name),
                    address_type=address_type,
                    value=value,
                    original_name=original_name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=raw.get("comment", ""),
                )
            )

    def _load_address_groups(self) -> None:
        for raw in self.client.cmdb.firewall.addrgrp.get():
            original_name = raw.get("name", "")
            if not original_name:
                continue
            # Sort members for stable diff: ManyToMany relationships are
            # unordered in Django, so the Nautobot adapter side returns
            # sorted; we must do the same here or every sync re-emits a
            # spurious "update" that just permutes the list.
            members = sorted(
                mangle_name(self.hostname, self.vdom, m["name"]) for m in raw.get("member", []) if "name" in m
            )
            self.add(
                self.address_object_group(
                    name=mangle_name(self.hostname, self.vdom, original_name),
                    members=members,
                    original_name=original_name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=raw.get("comment", ""),
                )
            )

    def _load_services(self) -> None:
        for raw in self.client.cmdb.firewall_service.custom.get():
            name = raw.get("name", "")
            if not name:
                continue
            ip_protocol, port = fortios_service_ports(raw)
            if ip_protocol is None:
                if self.job:
                    self.job.logger.warning(
                        f"Skipping service {name!r}: protocol {raw.get('protocol')!r} "
                        f"protocol-number {raw.get('protocol-number')!r} not mapped"
                    )
                continue
            # ServiceObject NK is (ip_protocol, port, name); no mangling.
            self.add(
                self.service_object(
                    name=name,
                    ip_protocol=ip_protocol,
                    port=port,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=raw.get("comment", ""),
                )
            )

    def _load_service_groups(self) -> None:
        # Service groups use composite identifiers for their members.
        # We need to look up the full natural key for each member — which
        # means the service must already be loaded. Build a name → NK map.
        svc_nk_by_name = {obj.name: (obj.ip_protocol, obj.port, obj.name) for obj in self.get_all(self.service_object)}
        for raw in self.client.cmdb.firewall_service.group.get():
            original_name = raw.get("name", "")
            if not original_name:
                continue
            members: list[tuple[str, str, str]] = []
            for m in raw.get("member", []):
                mn = m.get("name")
                if mn and mn in svc_nk_by_name:
                    members.append(svc_nk_by_name[mn])
                elif mn and self.job:
                    self.job.logger.warning(
                        f"Service group {original_name!r} references unknown service {mn!r} — skipping member"
                    )
            # Same canonical-ordering reason as address groups.
            members.sort()
            self.add(
                self.service_object_group(
                    name=mangle_name(self.hostname, self.vdom, original_name),
                    members=members,
                    original_name=original_name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=raw.get("comment", ""),
                )
            )

    def _load_policies(self) -> None:
        """Create one Policy per VDOM + one PolicyRule per FortiOS policy entry.

        Member resolution requires addresses, address groups, services, and
        service groups to already be loaded — that's why this runs last.
        """
        policy_name = mangle_name(self.hostname, self.vdom, "policy")
        self.add(
            self.policy(
                name=policy_name,
                vdom=self.vdom,
                hostname=self.hostname,
                description=f"FortiGate policies from {self.hostname} VDOM {self.vdom}",
            )
        )

        # Build lookup sets from already-loaded objects for member classification.
        # The store has MANGLED names; the FortiOS policy refs are raw.
        # split_policy_members mangles each raw ref before comparing.
        leaf_addr_names = {o.name for o in self.get_all(self.address_object)}
        grp_addr_names = {o.name for o in self.get_all(self.address_object_group)}
        grp_svc_names = {o.name for o in self.get_all(self.service_object_group)}
        svc_nk_by_name = {o.name: (o.ip_protocol, o.port, o.name) for o in self.get_all(self.service_object)}

        mangler = partial(mangle_name, self.hostname, self.vdom)

        for raw in self.client.cmdb.firewall.policy.get():
            policyid = raw.get("policyid")
            if policyid is None:
                continue
            original_name = raw.get("name", "") or f"policy_{policyid}"
            rule_name = mangler(f"rule_{policyid}")

            src_addrs, src_grps = split_policy_members(raw.get("srcaddr", []), leaf_addr_names, grp_addr_names, mangler)
            dst_addrs, dst_grps = split_policy_members(raw.get("dstaddr", []), leaf_addr_names, grp_addr_names, mangler)

            # Services: FortiOS service field is destination-side. Each
            # entry is either a ServiceObject (lookup by raw name in
            # svc_nk_by_name) or a ServiceObjectGroup (lookup by mangled
            # name in grp_svc_names).
            dst_svc_nks: list[tuple[str, str, str]] = []
            dst_svc_grp_names: list[str] = []
            for entry in raw.get("service", []):
                n = entry.get("name")
                if not n:
                    continue
                if n in svc_nk_by_name:
                    dst_svc_nks.append(svc_nk_by_name[n])
                elif mangler(n) in grp_svc_names:
                    dst_svc_grp_names.append(mangler(n))
                elif self.job:
                    self.job.logger.warning(
                        f"Policy {original_name!r} references unknown service {n!r} — dropping reference"
                    )
            dst_svc_nks.sort()
            dst_svc_grp_names.sort()

            action, action_note = fortios_action(raw.get("action", "deny"))
            log = raw.get("logtraffic", "disable") != "disable"

            # Interface names are now structured DiffSync attrs (v2.1+) AND
            # still embedded in description so humans see them in Nautobot UI.
            # The Nautobot adapter's parse step recovers them cleanly on load.
            srcintf = sorted(i.get("name") for i in raw.get("srcintf", []) if i.get("name"))
            dstintf = sorted(i.get("name") for i in raw.get("dstintf", []) if i.get("name"))

            description_parts = []
            if raw.get("comments"):
                description_parts.append(raw["comments"])
            if srcintf or dstintf:
                description_parts.append(f"[srcintf={','.join(srcintf) or '-'} dstintf={','.join(dstintf) or '-'}]")
            if action_note:
                description_parts.append(f"[{action_note}]")
            description = " ".join(description_parts)

            self.add(
                self.policy_rule(
                    name=rule_name,
                    policy_name=policy_name,
                    action=action,
                    log=log,
                    index=int(policyid),
                    original_name=original_name,
                    source_addresses=src_addrs,
                    source_address_groups=src_grps,
                    destination_addresses=dst_addrs,
                    destination_address_groups=dst_grps,
                    destination_services=dst_svc_nks,
                    destination_service_groups=dst_svc_grp_names,
                    source_interfaces=srcintf,
                    destination_interfaces=dstintf,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=description,
                )
            )

    def _load_nat_vips(self) -> None:
        """Sync ``firewall/vip`` entries as a singleton NATPolicy + NATPolicyRules.

        VIPs don't reference existing AddressObjects — they inline the
        ``extip`` and ``mappedip`` directly. We synthesize AddressObjects
        for each side using the convention
        ``<hostname>__<vdom>__vip_<vipname>_ext`` /  ``_mapped``, then
        link them from the NATPolicyRule. When port-forwarding is on,
        ServiceObjects are synthesized too.
        """
        # Create the singleton NATPolicy container.
        nat_policy_name = mangle_name(self.hostname, self.vdom, "nat_policy")
        self.add(
            self.nat_policy(
                name=nat_policy_name,
                vdom=self.vdom,
                hostname=self.hostname,
                description=f"FortiGate VIPs (DNAT) from {self.hostname} VDOM {self.vdom}",
            )
        )

        for raw in self.client.cmdb.firewall.vip.get():
            vip_name = raw.get("name", "")
            if not vip_name:
                continue
            extip = raw.get("extip", "")
            mappedip_list = raw.get("mappedip", [])
            if not extip or not mappedip_list:
                if self.job:
                    self.job.logger.warning(f"VIP {vip_name!r} missing extip or mappedip — skipping")
                continue

            # FortiOS mappedip is a list of {range: "..."}; for v1 we take
            # the first entry's range (single IP or "a.b.c.d-w.x.y.z").
            mapped_raw = (mappedip_list[0] or {}).get("range", "")
            if not mapped_raw:
                continue

            # Synthesize AddressObjects.
            ext_addr_name = mangle_name(self.hostname, self.vdom, f"vip_{vip_name}_ext")
            mapped_addr_name = mangle_name(self.hostname, self.vdom, f"vip_{vip_name}_mapped")
            self._upsert_address(
                ext_addr_name,
                "ipaddress",
                extip,
                f"VIP {vip_name} external IP",
            )
            mapped_type, mapped_value = _mapped_ip_to_address_value(mapped_raw)
            self._upsert_address(
                mapped_addr_name,
                mapped_type,
                mapped_value,
                f"VIP {vip_name} mapped IP",
            )

            orig_dst_svcs: list[tuple[str, str, str]] = []
            xlat_dst_svcs: list[tuple[str, str, str]] = []
            portforward = raw.get("portforward", "disable") == "enable"
            if portforward:
                proto = (raw.get("protocol", "tcp") or "tcp").upper()
                extport = str(raw.get("extport", "") or "")
                mappedport = str(raw.get("mappedport", "") or extport)
                if extport:
                    orig_svc_name = f"VIP_{vip_name}_ext"
                    self._upsert_service(orig_svc_name, proto, extport)
                    orig_dst_svcs.append((proto, extport, orig_svc_name))
                if mappedport:
                    xlat_svc_name = f"VIP_{vip_name}_mapped"
                    self._upsert_service(xlat_svc_name, proto, mappedport)
                    xlat_dst_svcs.append((proto, mappedport, xlat_svc_name))

            # Build the NATPolicyRule referencing the synthesized records.
            rule_name = mangle_name(self.hostname, self.vdom, f"nat_rule_{vip_name}")
            description_parts: list[str] = []
            if raw.get("comment"):
                description_parts.append(raw["comment"])
            extintf = raw.get("extintf", "")
            if extintf:
                description_parts.append(f"[extintf={extintf}]")
            if portforward:
                description_parts.append(f"[portforward {proto} {extport} -> {mappedport}]")
            description = " ".join(description_parts)

            self.add(
                self.nat_policy_rule(
                    name=rule_name,
                    nat_policy_name=nat_policy_name,
                    log=False,  # FortiOS VIPs don't have a log toggle on the VIP itself
                    index=0,  # FortiOS doesn't number VIPs the way it numbers policies
                    original_name=vip_name,
                    original_destination_addresses=[ext_addr_name],
                    translated_destination_addresses=[mapped_addr_name],
                    original_destination_services=sorted(orig_dst_svcs),
                    translated_destination_services=sorted(xlat_dst_svcs),
                    external_interface=extintf,
                    vdom=self.vdom,
                    hostname=self.hostname,
                    description=description,
                )
            )

    def _upsert_address(self, mangled_name: str, address_type: str, value: str, description: str) -> None:
        """Add a synthesized AddressObject to the store if not already present."""
        try:
            self.get(self.address_object, mangled_name)
            return  # already in store — e.g. from _load_addresses or earlier VIP
        except ObjectNotFound:
            pass
        self.add(
            self.address_object(
                name=mangled_name,
                address_type=address_type,
                value=value,
                original_name=mangled_name.split("__", 2)[-1],
                vdom=self.vdom,
                hostname=self.hostname,
                description=description,
            )
        )

    def _upsert_service(self, name: str, proto: str, port: str) -> None:
        try:
            self.get(
                self.service_object,
                {"ip_protocol": proto, "port": port, "name": name},
            )
            return
        except ObjectNotFound:
            pass
        self.add(
            self.service_object(
                name=name,
                ip_protocol=proto,
                port=port,
                vdom=self.vdom,
                hostname=self.hostname,
                description="Synthesized for FortiGate VIP port-forward",
            )
        )


def _mapped_ip_to_address_value(raw: str) -> tuple[str, str]:
    """Pick (address_type, value) for a FortiOS VIP mappedip range string.

    FortiOS represents mappedip as ``"a.b.c.d"`` (single host) or
    ``"a.b.c.d-w.x.y.z"`` (range). Returns the appropriate type+value
    for an AddressObject.
    """
    if "-" in raw:
        return "iprange", raw  # keep dash form
    return "ipaddress", raw


def _address_value(raw: dict) -> tuple[str | None, str]:
    """Pick the right (type, value) for a FortiOS address dict.

    Returns ``(None, "")`` for FortiOS types we don't model (geography,
    wildcard, dynamic, mac, etc.). Callers should skip those.
    """
    ftype = raw.get("type", "ipmask")  # FortiOS default is ipmask when omitted
    if ftype in ("ipmask", "interface-subnet"):
        # interface-subnet is a FortiOS dynamic type: the address
        # resolves to the subnet of a named interface. FortiOS includes
        # the *resolved* CIDR in the ``subnet`` field of the API response,
        # so from our perspective there's no difference at sync time
        # (verified against FortiOS 7.x on the FWF-61E).
        subnet = raw.get("subnet", "")
        if not subnet:
            return None, ""
        return "ipmask", fortios_subnet_to_cidr(subnet)
    if ftype == "fqdn":
        fqdn = raw.get("fqdn", "")
        return ("fqdn", fqdn) if fqdn else (None, "")
    if ftype == "iprange":
        start = raw.get("start-ip", "")
        end = raw.get("end-ip", "")
        if start and end:
            return "iprange", f"{start}-{end}"
        return None, ""
    if ftype == "ipaddress":
        ip = raw.get("subnet", "").split(" ", 1)[0]  # FortiOS stores host as 'a.b.c.d 255.255.255.255'
        return ("ipaddress", ip) if ip else (None, "")
    return None, ""
