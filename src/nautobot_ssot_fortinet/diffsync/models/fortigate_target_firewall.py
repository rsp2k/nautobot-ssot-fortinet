"""FortiGate-side DiffSync subclasses with CRUD — for PUSH (Nautobot → FortiGate).

The inverse of ``nautobot_firewall.py``: instead of writing to the
Nautobot ORM, these write to the FortiGate REST API via the adapter's
``client`` attribute.

**Scope (v0.2):**

- AddressObject — all 4 types (ipmask, fqdn, iprange, ipaddress)
- AddressObjectGroup — resolves members by un-mangled name
- ServiceObject — full inverse of pull-side mapping (TCP/UDP/SCTP, ICMP,
  ICMP6, IP-numbered protocols)
- ServiceObjectGroup — resolves members by composite NK

Policies and NAT are not yet push-enabled — those inherit the base
read-only classes whose CRUD methods are DiffSync no-ops.

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
    ServiceObject,
    ServiceObjectGroup,
)
from nautobot_ssot_fortinet.utils.fortios import (
    NAME_MANGLE_SEP,
    build_fortios_service_payload,
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

        adapter.client.cmdb.firewall.address.create(data=payload)
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
        self.adapter.client.cmdb.firewall.address.update(uid=original_name, data=payload)
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
        adapter.client.cmdb.firewall.addrgrp.create(data=payload)
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
        self.adapter.client.cmdb.firewall.addrgrp.update(uid=original, data=payload)
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
        adapter.client.cmdb.firewall_service.custom.create(data=payload)
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
        self.adapter.client.cmdb.firewall_service.custom.update(uid=self.name, data=payload)
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
        adapter.client.cmdb.firewall_service.group.create(data=payload)
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
        self.adapter.client.cmdb.firewall_service.group.update(uid=original, data=payload)
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


# ---- Helpers --------------------------------------------------------------


def _unmangle(mangled: str, hostname: str, vdom: str) -> str:
    """Strip the ``<hostname>__<vdom>__`` prefix to recover the original FortiOS name."""
    prefix = f"{hostname}{NAME_MANGLE_SEP}{vdom}{NAME_MANGLE_SEP}"
    return mangled[len(prefix) :] if mangled.startswith(prefix) else mangled
