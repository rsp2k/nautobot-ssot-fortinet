"""Nautobot-side DiffSync write models for the Device + Interface sync (v3.0).

CRUD subclasses of the FortiGateDevice / FortiGateInterface DiffSync models.
The Nautobot adapter swaps these in via class-level overrides so the same
DiffSync model is read-only on the FortiGate side and write-enabled on
the Nautobot side.
"""

from __future__ import annotations

from typing import Any

from nautobot_ssot_fortinet.diffsync.models.devices import (
    FortiGateDevice,
    FortiGateInterface,
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
            pfx, _ = Prefix.objects.get_or_create(
                prefix=network_cidr, namespace=ns, defaults={"status": active}
            )
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
