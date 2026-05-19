"""FortiGate-side DiffSync subclasses with CRUD for the PUSH direction (v3.3+).

Scope: **VLAN sub-interfaces only.**

Wrong writes to a FortiGate's interface table can:
  * Disconnect the appliance (set wrong IP on the management interface)
  * Misroute production traffic
  * Lock out administrators
  * Cause an STP-style switching loop

The safety boundary here is the design. The push-CRUD model:

  1. **Whitelists ONLY** ``type='virtual'`` interfaces with a populated
     ``parent_interface_name`` AND ``vlan_id`` in 1..4094. Anything that
     looks like a physical port, hard-switch, aggregate, or management
     interface is refused at write time with ``FortiOSAPIError`` —
     BEFORE any REST call goes out.
  2. **Hardcodes safe defaults** — ``allowaccess='ping'`` always; never
     pushes HTTPS/SSH/SNMP management access (operator can adjust on
     FortiOS UI after first sync if needed).
  3. **Operator opt-in twice** — the push Job's
     ``push_only_vlan_interfaces`` form var defaults True. Even with
     opt-in, the per-interface whitelist still applies.

Pull-side counterpart: ``NautobotFortiGateInterface`` (in
nautobot_devices.py) — same DiffSync identity, different write target.
"""

from __future__ import annotations

from typing import Any

from nautobot_ssot_fortinet.diffsync.models.devices import (
    FortiGateDevice,
    FortiGateInterface,
    FortiGateStaticRoute,
)
from nautobot_ssot_fortinet.utils.fortios import FortiOSAPIError, check_fortios_response


def _is_pushable_vlan_interface(attrs: dict[str, Any]) -> tuple[bool, str]:
    """Whitelist check — returns (True, "") if the interface is safe to push.

    Refuses everything that isn't a textbook VLAN sub-interface:

    - Non-virtual type — physical/hard-switch/aggregate/switch all refused
    - Missing parent_interface_name — bare interfaces aren't VLANs
    - vlan_id not in 1..4094 — outside the 802.1Q range

    Returns ``(False, reason)`` so callers can raise FortiOSAPIError with
    the precise reason for the refusal. Defense in depth: this check
    runs IN ADDITION to the type-discriminated entry-points in the
    target adapter.
    """
    if attrs.get("type") != "virtual":
        return False, f"refusing to push interface type={attrs.get('type')!r} (only VLAN sub-interfaces are pushable)"
    parent = attrs.get("parent_interface_name", "")
    if not parent:
        return False, "refusing to push interface with no parent_interface_name (not a VLAN sub-interface)"
    vlan_id = attrs.get("vlan_id")
    if not isinstance(vlan_id, int) or not (1 <= vlan_id <= 4094):
        return False, f"refusing to push interface with vlan_id={vlan_id!r} (must be 1..4094)"
    return True, ""


def _build_vlan_payload(name: str, attrs: dict[str, Any]) -> dict:
    """Build the FortiOS ``system.interface`` POST/PUT body for a VLAN sub-interface.

    Hardcoded safe defaults:
      * ``allowaccess='ping'`` — NEVER enables HTTPS/SSH/SNMP management.
        Operators wanting management access configure it on FortiOS UI
        after first sync.
      * ``comment`` always prefixed with the sync marker so operators can
        identify Nautobot-managed interfaces at a glance.
    """
    payload: dict[str, Any] = {
        "name": name,
        "type": "vlan",
        "interface": attrs["parent_interface_name"],
        "vlanid": attrs["vlan_id"],
        "vdom": attrs.get("vdom", "root"),
        "allowaccess": "ping",  # locked-down by design — see module docstring
        "status": "up" if attrs.get("enabled", True) else "down",
    }
    description = attrs.get("description", "") or ""
    payload["description"] = f"[Synced from Nautobot] {description}".strip()
    cidrs = attrs.get("cidrs") or []
    # Only push first CIDR; FortiOS supports secondaryip but our DiffSync
    # model treats cidrs as a sorted list — pushing the primary is
    # idempotent and unambiguous.
    if cidrs:
        host, mask = cidrs[0].split("/")
        # FortiOS expects "ip mask" (dotted) — convert prefix-length back
        import ipaddress

        net = ipaddress.IPv4Network(cidrs[0], strict=False)
        payload["ip"] = f"{host} {net.netmask}"
    if attrs.get("mtu"):
        payload["mtu-override"] = "enable"
        payload["mtu"] = attrs["mtu"]
    return payload


class FortiGateTargetDevice(FortiGateDevice):
    """Read-only on the push side — we don't push Device records back to FortiOS.

    Device records exist in Nautobot to anchor Interfaces + Routes; FortiOS
    has the canonical Device identity (its serial number) baked into the
    appliance itself. Pushing a "rename" or "change serial" would be
    nonsensical. This subclass exists so DiffSync's resolution machinery
    has a class to instantiate for fortigate_device diffs; create/update/
    delete are inherited base no-ops.
    """


class FortiGateTargetInterface(FortiGateInterface):
    """Push VLAN sub-interfaces to FortiGate. Refuses non-VLAN interfaces."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        """POST a new VLAN sub-interface to FortiGate."""
        pushable, reason = _is_pushable_vlan_interface(attrs)
        if not pushable:
            if adapter.job:
                adapter.job.logger.warning(f"Skipping push of {ids['name']!r}: {reason}")
            return super().create(adapter, ids, attrs)
        payload = _build_vlan_payload(ids["name"], attrs)
        check_fortios_response(
            adapter.client.cmdb.system.interface.create(data=payload),
            label=f"system.interface.create {ids['name']!r}",
        )
        if adapter.job:
            adapter.job.logger.info(
                f"  + created VLAN on FortiGate: {ids['name']!r} "
                f"(parent={attrs['parent_interface_name']}, vlanid={attrs['vlan_id']})"
            )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        """PUT updated fields back to FortiGate.

        Build the merged attribute set (current DiffSync state + changes)
        and rebuild the full payload — partial updates of relational
        fields like ``interface`` (parent) are error-prone in FortiOS.
        """
        merged = {
            "type": self.type,
            "parent_interface_name": attrs.get("parent_interface_name", self.parent_interface_name),
            "vlan_id": attrs.get("vlan_id", self.vlan_id),
            "vdom": self.vdom,
            "enabled": attrs.get("enabled", self.enabled),
            "description": attrs.get("description", self.description),
            "cidrs": attrs.get("cidrs", self.cidrs),
            "mtu": attrs.get("mtu", self.mtu),
        }
        pushable, reason = _is_pushable_vlan_interface(merged)
        if not pushable:
            if self.adapter.job:
                self.adapter.job.logger.warning(f"Skipping push update of {self.name!r}: {reason}")
            return super().update(attrs)
        payload = _build_vlan_payload(self.name, merged)
        check_fortios_response(
            self.adapter.client.cmdb.system.interface.update(data=payload),
            label=f"system.interface.update {self.name!r}",
        )
        if self.adapter.job:
            self.adapter.job.logger.info(f"  ~ updated VLAN on FortiGate: {self.name!r}")
        return super().update(attrs)

    def delete(self):
        """DELETE the VLAN sub-interface from FortiGate.

        Only proceeds if the interface is verifiably a VLAN sub-interface
        (per our cached DiffSync attrs). Defense in depth: even if the
        target adapter's load somehow inserted a non-VLAN record, the
        delete safety check would catch it before any REST call.
        """
        attrs = {
            "type": self.type,
            "parent_interface_name": self.parent_interface_name,
            "vlan_id": self.vlan_id,
        }
        pushable, reason = _is_pushable_vlan_interface(attrs)
        if not pushable:
            msg = f"Refusing to delete interface {self.name!r}: {reason}"
            if self.adapter.job:
                self.adapter.job.logger.error(msg)
            raise FortiOSAPIError(msg)
        check_fortios_response(
            self.adapter.client.cmdb.system.interface.delete(uid=self.name),
            label=f"system.interface.delete {self.name!r}",
        )
        if self.adapter.job:
            self.adapter.job.logger.info(f"  - deleted VLAN from FortiGate: {self.name!r}")
        super().delete()
        return self


class FortiGateTargetStaticRoute(FortiGateStaticRoute):
    """Read-only on the push side in v3.3 — route push deferred to v3.4+.

    Route push has similar risk profile (wrong route blackholes traffic)
    but the validation pattern is different from VLAN-interface push.
    Splitting the two releases keeps the v3.3 review surface focused on
    interface-only push semantics.
    """
