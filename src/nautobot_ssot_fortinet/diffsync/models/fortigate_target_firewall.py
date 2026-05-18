"""FortiGate-side DiffSync subclasses with CRUD — for PUSH (Nautobot → FortiGate).

The inverse of ``nautobot_firewall.py``: instead of writing to the
Nautobot ORM, these write to the FortiGate REST API via the adapter's
``client`` attribute.

**Scope (v2.1):**

- AddressObject — all 4 types (ipmask, fqdn, iprange, ipaddress), full CRUD
- AddressObjectGroup — full CRUD; resolves members by un-mangled name
- ServiceObject — full CRUD; full inverse of pull-side mapping
  (TCP/UDP/SCTP, ICMP, ICMP6, IP-numbered protocols)
- ServiceObjectGroup — full CRUD; resolves members by composite NK
- Policy — no-op container (FortiOS has no Policy concept)
- PolicyRule — **full CRUD** (v2.1). Create uses the structured
  ``source_interfaces`` / ``destination_interfaces`` DiffSync attrs to
  fill in FortiOS ``srcintf`` / ``dstintf``.
- NATPolicy — no-op container
- NATPolicyRule — **full CRUD** (v2.1) via VIP reconstruction. Create
  resolves the synthesized ``vip_*_ext`` / ``vip_*_mapped``
  AddressObjects back to their IP values for FortiOS ``extip`` /
  ``mappedip``; reads ``external_interface`` for ``extintf``.

**Name un-mangling:** Nautobot stores names as
``<hostname>__<vdom>__<original>``; FortiOS needs the *original* name.
``_unmangle()`` recovers it. The adapter is configured with the same
hostname/vdom that was used to mangle on the way in.
"""

from __future__ import annotations

from typing import Any

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
    NAME_MANGLE_SEP,
    build_fortios_service_payload,
    check_fortios_response,
)

# ---- AddressObject --------------------------------------------------------


class FortiGateAddressObject(AddressObject):
    """Push AddressObject (all 4 types) to FortiGate via ``cmdb/firewall/address``.

    The mangled DiffSync name gets un-mangled — we drop the
    ``<hostname>__<vdom>__`` prefix and POST/PUT with the original
    FortiOS-style name. The adapter is configured with the same hostname
    that's encoded in the names, so this is deterministic.

    FortiOS payload shapes per address_type:

        ipmask:    {"type": "ipmask",   "subnet": "10.0.10.0 255.255.255.0"}
        fqdn:      {"type": "fqdn",     "fqdn":   "example.com"}
        iprange:   {"type": "iprange",  "start-ip": "10.0.0.5", "end-ip": "10.0.0.10"}
        ipaddress: pushed as type=ipmask with /32 mask (single host)
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        """POST a new address to FortiGate."""
        payload = _address_payload(
            _unmangle(ids["name"], adapter.hostname, adapter.vdom),
            attrs.get("address_type", ""),
            attrs.get("value", ""),
            attrs.get("description", ""),
        )
        if payload is None:
            if adapter.job:
                adapter.job.logger.warning(
                    f"Skipping push of {ids['name']!r}: address_type={attrs.get('address_type')!r} not pushable"
                )
            return super().create(adapter, ids, attrs)

        check_fortios_response(
            adapter.client.cmdb.firewall.address.create(data=payload),
            label=f"address.create {payload['name']!r}",
        )
        if adapter.job:
            adapter.job.logger.info(
                f"  + created on FortiGate: {payload['name']!r} ({attrs['address_type']}, {attrs['value']})"
            )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        """PUT updated fields back to FortiGate."""
        original_name = _unmangle(self.name, self.adapter.hostname, self.adapter.vdom)
        # Always rebuild the full payload — partial updates of type-discriminated
        # fields are error-prone (changing fqdn → ipmask requires the whole shape).
        addr_type = attrs.get("address_type", self.address_type)
        value = attrs.get("value", self.value)
        description = attrs.get("description", self.description)
        payload = _address_payload(original_name, addr_type, value, description)
        if payload is None:
            return super().update(attrs)
        check_fortios_response(
            self.adapter.client.cmdb.firewall.address.update(data=payload),
            label=f"address.update {original_name!r}",
        )
        if self.adapter.job:
            self.adapter.job.logger.info(f"  ~ updated on FortiGate: {original_name!r}")
        return super().update(attrs)

    def delete(self):
        """DELETE the address from FortiGate."""
        original_name = _unmangle(self.name, self.adapter.hostname, self.adapter.vdom)
        self.adapter.client.cmdb.firewall.address.delete(uid=original_name)
        if self.adapter.job:
            self.adapter.job.logger.info(f"  - deleted from FortiGate: {original_name!r}")
        super().delete()
        return self


def _address_payload(name: str, address_type: str, value: str, description: str) -> dict | None:
    """Build a FortiOS firewall/address payload from our DiffSync attrs.

    Returns ``None`` for unrecognized address_type.
    """
    base = {"name": name, "comment": description[:255]}
    if address_type == "ipmask":
        base.update(type="ipmask", subnet=_cidr_to_fortios_subnet(value))
        return base
    if address_type == "fqdn":
        base.update(type="fqdn", fqdn=value)
        return base
    if address_type == "iprange":
        if "-" not in value:
            return None
        start, end = value.split("-", 1)
        base.update(type="iprange", **{"start-ip": start, "end-ip": end})
        return base
    if address_type == "ipaddress":
        # Single host — FortiOS modern API uses type=ipmask with /32.
        base.update(type="ipmask", subnet=_cidr_to_fortios_subnet(f"{value}/32"))
        return base
    return None


def _cidr_to_fortios_subnet(cidr: str) -> str:
    """``10.0.10.0/24`` → ``10.0.10.0 255.255.255.0`` (space-separated).

    >>> _cidr_to_fortios_subnet("10.0.10.0/24")
    '10.0.10.0 255.255.255.0'
    >>> _cidr_to_fortios_subnet("192.168.1.5/32")
    '192.168.1.5 255.255.255.255'
    >>> _cidr_to_fortios_subnet("0.0.0.0/0")
    '0.0.0.0 0.0.0.0'
    """
    import ipaddress

    net = ipaddress.IPv4Network(cidr, strict=False)
    return f"{net.network_address} {net.netmask}"


# ---- AddressObjectGroup ---------------------------------------------------


class FortiGateAddressObjectGroup(AddressObjectGroup):
    """Push AddressObjectGroup to FortiGate via ``cmdb/firewall/addrgrp``.

    Members are stored as the **mangled** AddressObject names; we
    un-mangle each one back to its original FortiOS name for the
    ``member`` array.
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        original = _unmangle(ids["name"], adapter.hostname, adapter.vdom)
        members = [{"name": _unmangle(m, adapter.hostname, adapter.vdom)} for m in attrs.get("members", [])]
        payload = {
            "name": original,
            "member": members,
            "comment": (attrs.get("description", "") or "")[:255],
        }
        check_fortios_response(
            adapter.client.cmdb.firewall.addrgrp.create(data=payload),
            label=f"addrgrp.create {original!r}",
        )
        if adapter.job:
            adapter.job.logger.info(f"  + created group on FortiGate: {original!r} ({len(members)} members)")
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        original = _unmangle(self.name, self.adapter.hostname, self.adapter.vdom)
        members = [
            {"name": _unmangle(m, self.adapter.hostname, self.adapter.vdom)} for m in attrs.get("members", self.members)
        ]
        payload = {
            "name": original,
            "member": members,
            "comment": (attrs.get("description", self.description) or "")[:255],
        }
        check_fortios_response(
            self.adapter.client.cmdb.firewall.addrgrp.update(data=payload),
            label=f"addrgrp.update {original!r}",
        )
        if self.adapter.job:
            self.adapter.job.logger.info(f"  ~ updated group on FortiGate: {original!r}")
        return super().update(attrs)

    def delete(self):
        original = _unmangle(self.name, self.adapter.hostname, self.adapter.vdom)
        self.adapter.client.cmdb.firewall.addrgrp.delete(uid=original)
        if self.adapter.job:
            self.adapter.job.logger.info(f"  - deleted group from FortiGate: {original!r}")
        super().delete()
        return self


# ---- ServiceObject --------------------------------------------------------


class FortiGateServiceObject(ServiceObject):
    """Push ServiceObject to FortiGate via ``cmdb/firewall.service/custom``.

    ServiceObject names are NOT mangled (composite natural key), so the
    DiffSync name IS the FortiOS name. The payload requires translating
    our (ip_protocol, port) back to FortiOS's protocol+sub-field shape
    via :func:`build_fortios_service_payload`.
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        payload = build_fortios_service_payload(
            name=ids["name"],
            ip_protocol=ids["ip_protocol"],
            port=ids["port"],
            description=attrs.get("description", "") or "",
        )
        if payload is None:
            if adapter.job:
                adapter.job.logger.warning(
                    f"Skipping push of service {ids['name']!r}: ip_protocol="
                    f"{ids['ip_protocol']!r} has no FortiOS mapping"
                )
            return super().create(adapter, ids, attrs)
        check_fortios_response(
            adapter.client.cmdb.firewall_service.custom.create(data=payload),
            label=f"service.create {payload['name']!r}",
        )
        if adapter.job:
            adapter.job.logger.info(
                f"  + created service on FortiGate: {ids['name']!r} ({ids['ip_protocol']}/{ids['port'] or '-'})"
            )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        payload = build_fortios_service_payload(
            name=self.name,
            ip_protocol=self.ip_protocol,
            port=self.port,
            description=attrs.get("description", self.description) or "",
        )
        if payload is None:
            return super().update(attrs)
        check_fortios_response(
            self.adapter.client.cmdb.firewall_service.custom.update(data=payload),
            label=f"service.update {self.name!r}",
        )
        if self.adapter.job:
            self.adapter.job.logger.info(f"  ~ updated service on FortiGate: {self.name!r}")
        return super().update(attrs)

    def delete(self):
        self.adapter.client.cmdb.firewall_service.custom.delete(uid=self.name)
        if self.adapter.job:
            self.adapter.job.logger.info(f"  - deleted service from FortiGate: {self.name!r}")
        super().delete()
        return self


# ---- ServiceObjectGroup ---------------------------------------------------


class FortiGateServiceObjectGroup(ServiceObjectGroup):
    """Push ServiceObjectGroup to FortiGate via ``cmdb/firewall.service/group``.

    Members are stored as ``(ip_protocol, port, name)`` composite NK
    tuples; we extract just the ``name`` for the FortiOS ``member`` array
    because FortiOS service group members are identified by service name.
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        original = _unmangle(ids["name"], adapter.hostname, adapter.vdom)
        # members is a list of (ip_protocol, port, name) tuples
        members = [{"name": nk[2]} for nk in attrs.get("members", [])]
        payload = {
            "name": original,
            "member": members,
            "comment": (attrs.get("description", "") or "")[:255],
        }
        check_fortios_response(
            adapter.client.cmdb.firewall_service.group.create(data=payload),
            label=f"service-group.create {original!r}",
        )
        if adapter.job:
            adapter.job.logger.info(f"  + created service group on FortiGate: {original!r} ({len(members)} members)")
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        original = _unmangle(self.name, self.adapter.hostname, self.adapter.vdom)
        members = [{"name": nk[2]} for nk in attrs.get("members", self.members)]
        payload = {
            "name": original,
            "member": members,
            "comment": (attrs.get("description", self.description) or "")[:255],
        }
        check_fortios_response(
            self.adapter.client.cmdb.firewall_service.group.update(data=payload),
            label=f"service-group.update {original!r}",
        )
        if self.adapter.job:
            self.adapter.job.logger.info(f"  ~ updated service group on FortiGate: {original!r}")
        return super().update(attrs)

    def delete(self):
        original = _unmangle(self.name, self.adapter.hostname, self.adapter.vdom)
        self.adapter.client.cmdb.firewall_service.group.delete(uid=original)
        if self.adapter.job:
            self.adapter.job.logger.info(f"  - deleted service group from FortiGate: {original!r}")
        super().delete()
        return self


# ---- Policy + PolicyRule --------------------------------------------------


# Inverse of FORTIOS_ACTION_MAP: Nautobot action → FortiOS action.
NAUTOBOT_ACTION_TO_FORTIOS: dict[str, str] = {
    "allow": "accept",
    "deny": "deny",
    "drop": "deny",  # firewall-models distinguishes; FortiOS rolls drop into deny
    "remark": "deny",  # remark = informational; closest FortiOS semantic is deny+no-log
}


class FortiGatePolicy(Policy):
    """Policy container — no-op on push.

    The FortiGate has no "Policy" concept; rules are pushed individually
    via ``FortiGatePolicyRule``. This subclass exists so that DiffSync's
    iteration doesn't trip on the policy_rule's parent reference.
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        return super().update(attrs)

    def delete(self):
        return super().delete()


class FortiGatePolicyRule(PolicyRule):
    """Push PolicyRule UPDATEs back to FortiGate.

    **Scope (v2.0):** UPDATE + DELETE only. Create is deferred to v2.1
    because FortiOS requires ``srcintf``/``dstintf`` on policy create, but
    those aren't stored as structured DiffSync attrs (they live in the
    rule's description for diagnostic purposes only).

    The update path is the **common operator workflow**: pull a policy
    into Nautobot, edit its allowed addresses/services/action/log in the
    Nautobot UI, push the change back. The FortiGate's existing
    srcintf/dstintf are preserved because we only update the fields we
    explicitly send.

    The DiffSync ``name`` is mangled as ``<host>__<vdom>__rule_<policyid>``;
    the FortiOS uid is the integer ``policyid`` (parsed from the suffix).
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        """POST a new policy to FortiGate (v2.1+).

        Uses the structured ``source_interfaces`` / ``destination_interfaces``
        DiffSync attrs to fill in FortiOS's required ``srcintf`` / ``dstintf``.
        If either is empty, falls back to a wildcard interface ``any``
        which FortiOS accepts for "match all interfaces."
        """
        policyid = _parse_policyid(ids["name"])
        if policyid is None:
            if adapter.job:
                adapter.job.logger.warning(f"Skipping create of {ids['name']!r}: can't parse policyid suffix")
            return super().create(adapter, ids, attrs)

        srcintf = attrs.get("source_interfaces") or ["any"]
        dstintf = attrs.get("destination_interfaces") or ["any"]
        payload = {
            "policyid": policyid,
            "name": attrs.get("original_name", "") or f"rule_{policyid}",
            "action": NAUTOBOT_ACTION_TO_FORTIOS.get(attrs.get("action", "deny"), "deny"),
            "status": "enable",
            "logtraffic": "all" if attrs.get("log") else "disable",
            "srcintf": [{"name": n} for n in srcintf],
            "dstintf": [{"name": n} for n in dstintf],
            "srcaddr": _addr_members(
                attrs.get("source_addresses", []),
                attrs.get("source_address_groups", []),
                adapter.hostname,
                adapter.vdom,
            ),
            "dstaddr": _addr_members(
                attrs.get("destination_addresses", []),
                attrs.get("destination_address_groups", []),
                adapter.hostname,
                adapter.vdom,
            ),
            "service": _svc_members(
                attrs.get("destination_services", []),
                attrs.get("destination_service_groups", []),
                adapter.hostname,
                adapter.vdom,
            ),
            "schedule": "always",
            "comments": (attrs.get("description", "") or "")[:255],
        }
        check_fortios_response(
            adapter.client.cmdb.firewall.policy.create(data=payload),
            label=f"policy.create policyid={policyid}",
        )
        if adapter.job:
            adapter.job.logger.info(
                f"  + created policy {policyid} on FortiGate ({srcintf} → {dstintf}, action={payload['action']})"
            )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        """PUT a partial policy update — only the fields the operator changed."""
        policyid = _parse_policyid(self.name)
        if policyid is None:
            if self.adapter.job:
                self.adapter.job.logger.warning(f"Cannot derive policyid from {self.name!r}; skipping update")
            return super().update(attrs)

        payload: dict[str, Any] = {}
        if "action" in attrs:
            payload["action"] = NAUTOBOT_ACTION_TO_FORTIOS.get(attrs["action"], "deny")
        if "log" in attrs:
            payload["logtraffic"] = "all" if attrs["log"] else "disable"
        if "source_addresses" in attrs or "source_address_groups" in attrs:
            payload["srcaddr"] = _addr_members(
                attrs.get("source_addresses", self.source_addresses),
                attrs.get("source_address_groups", self.source_address_groups),
                self.adapter.hostname,
                self.adapter.vdom,
            )
        if "destination_addresses" in attrs or "destination_address_groups" in attrs:
            payload["dstaddr"] = _addr_members(
                attrs.get("destination_addresses", self.destination_addresses),
                attrs.get("destination_address_groups", self.destination_address_groups),
                self.adapter.hostname,
                self.adapter.vdom,
            )
        if "destination_services" in attrs or "destination_service_groups" in attrs:
            payload["service"] = _svc_members(
                attrs.get("destination_services", self.destination_services),
                attrs.get("destination_service_groups", self.destination_service_groups),
                self.adapter.hostname,
                self.adapter.vdom,
            )

        if not payload:
            return super().update(attrs)

        # fortigate-api's Connector.update() pulls the uid (here: policyid)
        # from inside data — there's no separate uid= kwarg.
        payload["policyid"] = policyid
        check_fortios_response(
            self.adapter.client.cmdb.firewall.policy.update(data=payload),
            label=f"policy.update policyid={policyid}",
        )
        if self.adapter.job:
            self.adapter.job.logger.info(f"  ~ updated policy {policyid} on FortiGate: {sorted(payload)}")
        return super().update(attrs)

    def delete(self):
        """DELETE the policy by policyid."""
        policyid = _parse_policyid(self.name)
        if policyid is None:
            return super().delete()
        self.adapter.client.cmdb.firewall.policy.delete(uid=str(policyid))
        if self.adapter.job:
            self.adapter.job.logger.info(f"  - deleted policy {policyid} from FortiGate")
        super().delete()
        return self


def _parse_policyid(mangled_rule_name: str) -> int | None:
    """Extract the integer FortiOS policyid from a mangled rule name.

    Mangled form: ``<host>__<vdom>__rule_<N>``.

    >>> _parse_policyid("fgt-edge1__root__rule_42")
    42
    >>> _parse_policyid("fgt-edge1__root__not_a_rule")  # returns None
    """
    if "__rule_" not in mangled_rule_name:
        return None
    suffix = mangled_rule_name.rsplit("__rule_", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return None


def _addr_members(leaf_names: list, group_names: list, hostname: str, vdom: str) -> list:
    """Build the FortiOS ``member`` list for a policy's srcaddr/dstaddr."""
    out = [{"name": _unmangle(n, hostname, vdom)} for n in (list(leaf_names) + list(group_names))]
    return out


def _svc_members(svc_nks: list, svc_group_names: list, hostname: str, vdom: str) -> list:
    """Build the FortiOS ``service`` member list for a policy.

    ServiceObjects don't get name-mangled (composite NK), so we use the
    name directly. Service groups DO get mangled.
    """
    out = []
    for nk in svc_nks:
        # nk is (ip_protocol, port, name)
        out.append({"name": nk[2]})
    for grp_name in svc_group_names:
        out.append({"name": _unmangle(grp_name, hostname, vdom)})
    return out


# ---- NATPolicy + NATPolicyRule (VIP reconstruction) ----------------------


class FortiGateNATPolicy(NATPolicy):
    """NATPolicy container — no-op on push (FortiOS has no NATPolicy concept)."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        return super().update(attrs)

    def delete(self):
        return super().delete()


class FortiGateNATPolicyRule(NATPolicyRule):
    """Push NATPolicyRule UPDATEs/DELETEs back to FortiGate as VIPs.

    **Scope (v2.0):** UPDATE + DELETE. Create is deferred to v2.1 (full
    VIP reconstruction from scratch requires the operator to specify
    extintf, which isn't stored as a DiffSync attr).

    The mangled DiffSync name is ``<host>__<vdom>__nat_rule_<vipname>``;
    the FortiOS uid is the VIP name (un-mangled from the suffix). The
    update path can change the mapped IP, ports (if portforward), and
    description without touching extintf.
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        """POST a new VIP to FortiGate (v2.1+).

        Reconstructs a full ``firewall/vip`` payload from the DiffSync
        attrs — uses ``external_interface`` for ``extintf`` (defaulting
        to ``any``), resolves the synthesized ``vip_*_ext`` /
        ``vip_*_mapped`` AddressObjects back to their IP values for the
        FortiOS ``extip`` / ``mappedip`` fields.
        """
        # Recover the original FortiOS VIP name from the mangled suffix.
        if "__nat_rule_" not in ids["name"]:
            if adapter.job:
                adapter.job.logger.warning(
                    f"Skipping NATPolicyRule {ids['name']!r}: name doesn't follow "
                    f"the expected '__nat_rule_<vipname>' convention"
                )
            return super().create(adapter, ids, attrs)
        vip_name = ids["name"].rsplit("__nat_rule_", 1)[-1]

        ext_addrs = attrs.get("original_destination_addresses", [])
        mapped_addrs = attrs.get("translated_destination_addresses", [])
        if not (ext_addrs and mapped_addrs):
            if adapter.job:
                adapter.job.logger.warning(f"Skipping VIP {vip_name!r} create: missing ext/mapped addresses")
            return super().create(adapter, ids, attrs)

        extip = _lookup_synth_addr_value(adapter, ext_addrs[0])
        mappedip = _lookup_synth_addr_value(adapter, mapped_addrs[0])
        if not (extip and mappedip):
            if adapter.job:
                adapter.job.logger.warning(
                    f"Skipping VIP {vip_name!r}: couldn't resolve synthesized "
                    f"AddressObject values (ext={extip}, mapped={mappedip})"
                )
            return super().create(adapter, ids, attrs)

        payload: dict[str, Any] = {
            "name": vip_name,
            "extip": extip,
            "extintf": attrs.get("external_interface") or "any",
            "mappedip": [{"range": mappedip}],
            "comment": (attrs.get("description", "") or "")[:255],
        }

        # Port-forward: read from translated_destination_services if set.
        xlat_svcs = attrs.get("translated_destination_services", [])
        orig_svcs = attrs.get("original_destination_services", [])
        if xlat_svcs and orig_svcs:
            payload["portforward"] = "enable"
            payload["protocol"] = orig_svcs[0][0].lower()
            payload["extport"] = orig_svcs[0][1]
            payload["mappedport"] = xlat_svcs[0][1]

        check_fortios_response(
            adapter.client.cmdb.firewall.vip.create(data=payload),
            label=f"vip.create {payload['name']!r}",
        )
        if adapter.job:
            adapter.job.logger.info(f"  + created VIP {vip_name!r} on FortiGate ({extip} → {mappedip})")
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        """PUT a partial VIP update — mapped IP and ports only."""
        vip_name = self.name.rsplit("__nat_rule_", 1)[-1]
        if vip_name == self.name:  # no separator found
            return super().update(attrs)

        payload: dict[str, Any] = {}

        # Translated destination → FortiOS mappedip (single-IP range).
        if "translated_destination_addresses" in attrs:
            mapped_addrs = attrs["translated_destination_addresses"]
            if mapped_addrs:
                # Look up the synthesized address record to get its actual value.
                ip = _lookup_synth_addr_value(self.adapter, mapped_addrs[0])
                if ip:
                    payload["mappedip"] = [{"range": ip}]

        # Translated destination service → FortiOS mappedport (port forward).
        if "translated_destination_services" in attrs:
            svcs = attrs["translated_destination_services"]
            if svcs:
                # nk is (ip_protocol, port, name)
                payload["mappedport"] = svcs[0][1]

        if "original_destination_services" in attrs:
            svcs = attrs["original_destination_services"]
            if svcs:
                payload["extport"] = svcs[0][1]
                payload["protocol"] = svcs[0][0].lower()  # FortiOS lowercases
                payload["portforward"] = "enable"

        if not payload:
            return super().update(attrs)

        # fortigate-api requires uid (here: name) inside data, not as kwarg.
        payload["name"] = vip_name
        check_fortios_response(
            self.adapter.client.cmdb.firewall.vip.update(data=payload),
            label=f"vip.update {vip_name!r}",
        )
        if self.adapter.job:
            self.adapter.job.logger.info(f"  ~ updated VIP {vip_name!r} on FortiGate: {sorted(payload)}")
        return super().update(attrs)

    def delete(self):
        """DELETE the VIP from FortiGate."""
        vip_name = self.name.rsplit("__nat_rule_", 1)[-1]
        if vip_name == self.name:
            return super().delete()
        self.adapter.client.cmdb.firewall.vip.delete(uid=vip_name)
        if self.adapter.job:
            self.adapter.job.logger.info(f"  - deleted VIP {vip_name!r} from FortiGate")
        super().delete()
        return self


def _lookup_synth_addr_value(adapter, mangled_name: str) -> str | None:
    """For a synthesized vip_<x>_mapped AddressObject in our store, return its IP value.

    The push-side adapter's load() includes AddressObjects, so the
    synthesized records pulled into the store carry their resolved IP
    value as the DiffSync ``value`` attr.
    """
    try:
        addr = adapter.get(adapter.address_object, mangled_name)
    except Exception:  # noqa: BLE001 — ObjectNotFound or anything
        return None
    return addr.value or None


# ---- Helpers --------------------------------------------------------------


def _unmangle(mangled: str, hostname: str, vdom: str) -> str:
    """Strip the ``<hostname>__<vdom>__`` prefix to recover the original FortiOS name."""
    prefix = f"{hostname}{NAME_MANGLE_SEP}{vdom}{NAME_MANGLE_SEP}"
    return mangled[len(prefix) :] if mangled.startswith(prefix) else mangled
