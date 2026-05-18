"""Nautobot-side DiffSync write models for the Device + Interface + Route sync (v3.0+v3.1).

CRUD subclasses of the FortiGateDevice / FortiGateInterface /
FortiGateStaticRoute DiffSync models. The Nautobot adapter swaps these in
via class-level overrides so the same DiffSync model is read-only on the
FortiGate side and write-enabled on the Nautobot side.

v3.1 additions:

- ``NautobotFortiGateInterface`` now resolves ``parent_interface_name``
  to a ``parent_interface`` FK and creates/attaches ``ipam.VLAN`` records
  when ``vlan_id`` is set.
- ``NautobotFortiGateStaticRoute`` is new — writes to the
  ``FortinetStaticRoute`` Django model introduced in this release.
"""

from __future__ import annotations

from typing import Any

from nautobot_ssot_fortinet.diffsync.models.devices import (
    FortiGateDevice,
    FortiGateInterface,
    FortiGateStaticRoute,
)


class NautobotFortiGateDevice(FortiGateDevice):
    """Create/update the FortiGate as a Nautobot ``dcim.Device``."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.dcim.models import Device, DeviceType, Location
        from nautobot.extras.models import Role, Status

        Device.objects.update_or_create(
            name=ids["name"],
            defaults={
                "serial": attrs.get("serial", ""),
                "device_type": DeviceType.objects.get(model=attrs["device_type_model"]),
                "role": Role.objects.get(name=attrs["role_name"]),
                "location": Location.objects.get(name=attrs["location_name"]),
                "status": Status.objects.get(name=attrs["status_name"]),
            },
        )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot.dcim.models import Device

        try:
            dev = Device.objects.get(name=self.name)
        except Device.DoesNotExist:
            return super().update(attrs)
        if "serial" in attrs:
            dev.serial = attrs["serial"]
        dev.save()
        return super().update(attrs)

    def delete(self):
        # Deleting the Device cascades to its Interfaces and IP assignments.
        # Operators who want to detach from Nautobot without losing history
        # should use Nautobot's UI to set status=Decommissioning instead.
        from nautobot.dcim.models import Device

        Device.objects.filter(name=self.name).delete()
        super().delete()
        return self


class NautobotFortiGateInterface(FortiGateInterface):
    """Create/update Nautobot ``dcim.Interface`` records on the synced Device.

    IP assignments (from the ``cidrs`` attr) are materialized as
    ``ipam.IPAddress`` records and attached via ``interface.ip_addresses``.
    Each IP needs a parent ``ipam.Prefix`` of exact mask length (Nautobot
    3.x requirement); we create that on the fly if it doesn't exist.
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.dcim.models import Device, Interface
        from nautobot.extras.models import Status

        active = Status.objects.get(name="Active")
        try:
            device = Device.objects.get(name=ids["device_name"])
        except Device.DoesNotExist:
            if adapter.job:
                adapter.job.logger.warning(
                    f"Skipping interface {ids['name']!r}: parent Device {ids['device_name']!r} doesn't exist yet"
                )
            return super().create(adapter, ids, attrs)

        iface, _ = Interface.objects.update_or_create(
            device=device,
            name=ids["name"],
            defaults={
                "type": attrs["type"],
                "enabled": attrs.get("enabled", True),
                "mtu": attrs.get("mtu"),
                "description": attrs.get("description", ""),
                "status": active,
            },
        )

        # v3.1: VLAN sub-interface attrs
        cls._apply_vlan_attrs(iface, device, attrs, adapter)

        # Materialize CIDRs as IPAddresses and attach
        cls._sync_ip_addresses(iface, attrs.get("cidrs", []))
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot.dcim.models import Device, Interface

        try:
            device = Device.objects.get(name=self.device_name)
            iface = Interface.objects.get(device=device, name=self.name)
        except (Device.DoesNotExist, Interface.DoesNotExist):
            return super().update(attrs)

        if "type" in attrs:
            iface.type = attrs["type"]
        if "enabled" in attrs:
            iface.enabled = attrs["enabled"]
        if "mtu" in attrs:
            iface.mtu = attrs["mtu"]
        if "description" in attrs:
            iface.description = attrs["description"]
        iface.save()

        # v3.1: re-apply VLAN attrs if any of them changed
        if any(k in attrs for k in ("parent_interface_name", "vlan_id", "vlan_mode")):
            # Build a merged attrs dict — DiffSync only passes changed keys,
            # so we fill missing ones from the current DiffSync state to
            # avoid clobbering with empty values.
            merged = {
                "parent_interface_name": attrs.get("parent_interface_name", self.parent_interface_name),
                "vlan_id": attrs.get("vlan_id", self.vlan_id),
                "vlan_mode": attrs.get("vlan_mode", self.vlan_mode),
            }
            self._apply_vlan_attrs(iface, device, merged, self.adapter)

        if "cidrs" in attrs:
            self._sync_ip_addresses(iface, attrs["cidrs"])
        return super().update(attrs)

    def delete(self):
        from nautobot.dcim.models import Device, Interface

        try:
            device = Device.objects.get(name=self.device_name)
        except Device.DoesNotExist:
            return super().delete()
        Interface.objects.filter(device=device, name=self.name).delete()
        super().delete()
        return self

    @staticmethod
    def _apply_vlan_attrs(iface, device, attrs: dict[str, Any], adapter) -> None:
        """Wire up parent_interface FK + untagged_vlan FK for VLAN sub-interfaces.

        Called from both ``create()`` and ``update()`` so the resolution
        logic lives in one place. Skipped (no writes) when the interface
        isn't a VLAN — detected by ``vlan_id is None`` AND
        ``parent_interface_name == ''``.

        Parent-interface resolution is best-effort: if the parent doesn't
        exist yet in Nautobot (load ordering quirk on first sync), we log
        and skip the FK assignment — the next sync run will fix it once
        the parent record exists.
        """
        from nautobot.dcim.models import Interface
        from nautobot.extras.models import Status
        from nautobot.ipam.models import VLAN

        vlan_id = attrs.get("vlan_id")
        parent_name = attrs.get("parent_interface_name", "")
        if vlan_id is None and not parent_name:
            return  # Not a VLAN interface — nothing to do

        # Resolve parent interface (sub-interface needs a parent FK)
        if parent_name:
            try:
                parent = Interface.objects.get(device=device, name=parent_name)
                iface.parent_interface = parent
            except Interface.DoesNotExist:
                if adapter and adapter.job:
                    adapter.job.logger.warning(
                        f"VLAN {iface.name!r}: parent interface {parent_name!r} not yet in Nautobot; "
                        "FK will be set on next sync run"
                    )

        # Create/attach VLAN record + set as untagged on the sub-interface
        if vlan_id is not None:
            active = Status.objects.get(name="Active")
            vlan_name = f"{device.name}-vlan{vlan_id}"
            vlan, _ = VLAN.objects.get_or_create(
                vid=vlan_id,
                name=vlan_name,
                defaults={"status": active},
            )
            iface.untagged_vlan = vlan
            mode = attrs.get("vlan_mode", "tagged")
            # Nautobot InterfaceModeChoices: TAGGED, ACCESS, TAGGED_ALL
            iface.mode = "access" if mode == "access" else "tagged"

        iface.save()

    @staticmethod
    def _sync_ip_addresses(iface, cidrs: list[str]) -> None:
        """Idempotent-replace the interface's IP assignments to match cidrs.

        Each ``host/mask`` CIDR becomes an ``ipam.IPAddress``. The parent
        ``ipam.Prefix`` is looked up by NETWORK address (Nautobot stores
        prefixes in network form, so ``203.0.113.99/24`` and
        ``203.0.113.0/24`` resolve to the same Prefix record — we use
        the network form for lookup to avoid duplicate-key conflicts).

        Removes any existing assignments not in the target list (drift cleanup).
        """
        import ipaddress

        from nautobot.extras.models import Status
        from nautobot.ipam.models import IPAddress, Namespace, Prefix

        active = Status.objects.get(name="Active")
        ns = Namespace.objects.get(name="Global")

        wanted_hosts = set()
        for cidr in cidrs:
            try:
                host, mask_str = cidr.split("/")
                mask = int(mask_str)
                # Compute network form for the parent Prefix lookup
                network_cidr = str(ipaddress.IPv4Network(cidr, strict=False))
            except (ValueError, IndexError):
                continue
            pfx, _ = Prefix.objects.get_or_create(prefix=network_cidr, namespace=ns, defaults={"status": active})
            ip, _ = IPAddress.objects.get_or_create(
                host=host,
                defaults={"status": active, "mask_length": mask, "parent": pfx},
            )
            iface.ip_addresses.add(ip)
            wanted_hosts.add(host)

        # Remove IPs that are no longer in the target list (drift cleanup)
        for ip in iface.ip_addresses.all():
            if ip.host not in wanted_hosts:
                iface.ip_addresses.remove(ip)


class NautobotFortiGateStaticRoute(FortiGateStaticRoute):
    """Create/update/delete ``FortinetStaticRoute`` Django records (v3.1).

    Composite identity ``(device_name, vdom, seq_num)``. The FortiOS
    interface name is resolved to a Nautobot Interface FK at write time;
    if the Interface doesn't exist yet (load ordering, or operator pushed
    a route that references a deleted interface), we leave the FK null
    and log a warning rather than failing the whole route create.

    Routes are CASCADE-deleted with their parent Device, so the explicit
    delete() here is rarely the path that fires — but it's needed for the
    "operator deleted just this route from the device" case.
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.dcim.models import Device

        from nautobot_ssot_fortinet.models import FortinetStaticRoute

        try:
            device = Device.objects.get(name=ids["device_name"])
        except Device.DoesNotExist:
            if adapter.job:
                adapter.job.logger.warning(
                    f"Skipping route seq={ids['seq_num']!r}: "
                    f"Device {ids['device_name']!r} doesn't exist in Nautobot yet"
                )
            return super().create(adapter, ids, attrs)

        iface = cls._resolve_interface(device, attrs.get("interface_name", ""), adapter)

        FortinetStaticRoute.objects.update_or_create(
            device=device,
            vdom=ids["vdom"],
            seq_num=ids["seq_num"],
            defaults={
                "destination": attrs["destination"],
                "gateway": attrs.get("gateway") or None,
                "interface": iface,
                "distance": attrs.get("distance", 10),
                "priority": attrs.get("priority", 0),
                "blackhole": attrs.get("blackhole", False),
                "comment": attrs.get("comment", ""),
            },
        )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot.dcim.models import Device

        from nautobot_ssot_fortinet.models import FortinetStaticRoute

        try:
            device = Device.objects.get(name=self.device_name)
            route = FortinetStaticRoute.objects.get(device=device, vdom=self.vdom, seq_num=self.seq_num)
        except (Device.DoesNotExist, FortinetStaticRoute.DoesNotExist):
            return super().update(attrs)

        if "destination" in attrs:
            route.destination = attrs["destination"]
        if "gateway" in attrs:
            route.gateway = attrs["gateway"] or None
        if "interface_name" in attrs:
            route.interface = self._resolve_interface(device, attrs["interface_name"], self.adapter)
        if "distance" in attrs:
            route.distance = attrs["distance"]
        if "priority" in attrs:
            route.priority = attrs["priority"]
        if "blackhole" in attrs:
            route.blackhole = attrs["blackhole"]
        if "comment" in attrs:
            route.comment = attrs["comment"]
        route.save()
        return super().update(attrs)

    def delete(self):
        from nautobot.dcim.models import Device

        from nautobot_ssot_fortinet.models import FortinetStaticRoute

        try:
            device = Device.objects.get(name=self.device_name)
        except Device.DoesNotExist:
            return super().delete()
        FortinetStaticRoute.objects.filter(device=device, vdom=self.vdom, seq_num=self.seq_num).delete()
        super().delete()
        return self

    @staticmethod
    def _resolve_interface(device, interface_name: str, adapter):
        """Return the Interface FK for ``interface_name``, or None with a warning.

        Routes with ``interface_name=""`` (blackhole, RIB-resolved) return
        None silently. Routes referencing an unknown interface name return
        None and log — the route still gets created (just without FK
        linkage); next sync will fix it once the interface is loaded.
        """
        from nautobot.dcim.models import Interface

        if not interface_name:
            return None
        try:
            return Interface.objects.get(device=device, name=interface_name)
        except Interface.DoesNotExist:
            if adapter and adapter.job:
                adapter.job.logger.warning(
                    f"Route on {device.name!r} references interface "
                    f"{interface_name!r} which doesn't exist in Nautobot — "
                    "FK left null, will resolve on next sync"
                )
            return None
