"""Nautobot-side DiffSync adapter for the Device + Interface + Route sync (v3.0 + v3.1).

Reads existing Nautobot ``dcim.Device`` + ``dcim.Interface`` records
scoped to the target Device (by name = ExternalIntegration name) and —
in v3.1+ — existing ``FortinetStaticRoute`` records anchored to that
Device. The sync is pull-only, so this adapter only needs read+CRUD on
the target Device's children — not on Devices globally.
"""

from __future__ import annotations

from diffsync import Adapter

from nautobot_ssot_fortinet.diffsync.models.nautobot_devices import (
    NautobotFortiGateDevice,
    NautobotFortiGateInterface,
    NautobotFortiGateStaticRoute,
)


class NautobotDevicesAdapter(Adapter):
    """Read Nautobot's view of the FortiGate Device, interfaces, and routes."""

    fortigate_device = NautobotFortiGateDevice
    fortigate_interface = NautobotFortiGateInterface
    fortigate_static_route = NautobotFortiGateStaticRoute

    top_level = ("fortigate_device", "fortigate_interface", "fortigate_static_route")

    def __init__(
        self,
        *,
        hostname: str,
        vdom: str = "root",
        device_type_model: str = "",
        role_name: str = "",
        location_name: str = "",
        status_name: str = "Active",
        include_static_routes: bool = True,
        job=None,
        sync=None,
    ):
        super().__init__()
        self.hostname = hostname
        self.vdom = vdom
        # The form-var scoping values are echoed onto the adapter so
        # diff comparisons against the source-side device record match
        # (otherwise every push would show a phantom "create" diff for
        # the Device because the target's device_type/role/etc fields
        # would be loaded from the ORM differently).
        self.device_type_model = device_type_model
        self.role_name = role_name
        self.location_name = location_name
        self.status_name = status_name
        self.include_static_routes = include_static_routes
        self.job = job
        self.sync = sync

    def load(self) -> None:
        """Read the FortiGate's Device record (if it exists) and its interfaces + routes."""
        from nautobot.dcim.models import Device

        try:
            device = Device.objects.get(name=self.hostname)
        except Device.DoesNotExist:
            # First-run case: no Device record yet. Sync will create one
            # via the diff. Nothing to load on the Nautobot side.
            if self.job:
                self.job.logger.info(
                    f"No existing Device {self.hostname!r} in Nautobot — will be created on first sync."
                )
            return

        self.add(
            self.fortigate_device(
                name=device.name,
                serial=device.serial or "",
                device_type_model=device.device_type.model,
                role_name=device.role.name if device.role else "",
                location_name=device.location.name if device.location else "",
                status_name=device.status.name if device.status else "",
                vdom=self.vdom,
            )
        )

        for iface in device.interfaces.all():
            # Echo the IPs assigned to this interface as CIDR strings,
            # matching the format the FortiGate-side adapter emits.
            cidrs = []
            for ip in iface.ip_addresses.all():
                cidrs.append(f"{ip.host}/{ip.mask_length}")
            cidrs.sort()

            # v3.1: echo VLAN attrs back so the diff against the source
            # side matches when nothing has changed.
            parent_name = iface.parent_interface.name if iface.parent_interface else ""
            vlan_id = iface.untagged_vlan.vid if iface.untagged_vlan else None
            vlan_mode = iface.mode if vlan_id is not None else ""

            self.add(
                self.fortigate_interface(
                    device_name=device.name,
                    name=iface.name,
                    type=iface.type,
                    enabled=iface.enabled,
                    mtu=iface.mtu,
                    description=iface.description or "",
                    vdom=self.vdom,
                    cidrs=cidrs,
                    parent_interface_name=parent_name,
                    vlan_id=vlan_id,
                    vlan_mode=vlan_mode,
                )
            )

        if self.include_static_routes:
            self._load_static_routes(device)

    def _load_static_routes(self, device) -> None:
        """Load existing FortinetStaticRoute records for this Device + vdom."""
        from nautobot_ssot_fortinet.models import FortinetStaticRoute

        for route in FortinetStaticRoute.objects.filter(device=device, vdom=self.vdom):
            self.add(
                self.fortigate_static_route(
                    device_name=device.name,
                    vdom=route.vdom,
                    seq_num=route.seq_num,
                    destination=route.destination,
                    gateway=route.gateway or "",
                    interface_name=route.interface.name if route.interface else "",
                    distance=route.distance,
                    priority=route.priority,
                    blackhole=route.blackhole,
                    comment=route.comment or "",
                )
            )
