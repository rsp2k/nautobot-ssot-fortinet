"""Nautobot-side DiffSync adapter for wireless objects.

Scoped by name prefix the same way ``nautobot_firewall`` is. AP Device
records are scoped by their ``serial`` matching the set of serials the
source side reports — not by name prefix (Devices have global name
uniqueness and may legitimately exist with FortiGate-prefixed names from
other tools).
"""

from __future__ import annotations

from diffsync import Adapter

from nautobot_ssot_fortinet.diffsync.models.nautobot_wireless import (
    NautobotAccessPoint,
    NautobotRadioProfile,
    NautobotWirelessNetwork,
)
from nautobot_ssot_fortinet.utils.fortios import NAME_MANGLE_SEP


class NautobotWirelessAdapter(Adapter):
    """Read Nautobot wireless ORM, scoped by hostname + vdom name prefix."""

    wireless_network = NautobotWirelessNetwork
    radio_profile = NautobotRadioProfile
    access_point = NautobotAccessPoint

    top_level = ("wireless_network", "radio_profile", "access_point")

    def __init__(
        self,
        *args,
        hostname: str,
        vdom: str = "root",
        sync_access_points: bool = False,
        ap_device_type_model: str = "",
        ap_role_name: str = "",
        ap_location_name: str = "",
        ap_status_name: str = "Active",
        job=None,
        sync=None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.job = job
        self.sync = sync
        self.hostname = hostname
        self.vdom = vdom
        self.sync_access_points = sync_access_points
        self.ap_device_type_model = ap_device_type_model
        self.ap_role_name = ap_role_name
        self.ap_location_name = ap_location_name
        self.ap_status_name = ap_status_name
        self.name_prefix = f"{hostname}{NAME_MANGLE_SEP}{vdom}{NAME_MANGLE_SEP}"

    def load(self) -> None:
        """Pull this FortiGate's wireless records from Nautobot."""
        from nautobot.wireless.models import (
            RadioProfile as ORMRadioProfile,
        )
        from nautobot.wireless.models import (
            WirelessNetwork as ORMWirelessNetwork,
        )

        self._load_wireless_networks(ORMWirelessNetwork)
        self._load_radio_profiles(ORMRadioProfile)
        if self.sync_access_points:
            self._load_access_points()

    def _load_wireless_networks(self, model) -> None:
        for orm in model.objects.filter(name__startswith=self.name_prefix):
            original_name = orm.name[len(self.name_prefix) :]
            description = _strip_description_prefix(orm.description or "", original_name)
            self.add(
                self.wireless_network(
                    name=orm.name,
                    ssid=orm.ssid,
                    mode=orm.mode,
                    enabled=orm.enabled,
                    authentication=orm.authentication,
                    hidden=orm.hidden,
                    description=description,
                    original_name=original_name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                )
            )

    def _load_radio_profiles(self, model) -> None:
        for orm in model.objects.filter(name__startswith=self.name_prefix):
            # Recover the original profile name + radio index from the mangled name.
            # Mangled form: <hostname>__<vdom>__<profile>__radio<N>
            remainder = orm.name[len(self.name_prefix) :]
            try:
                original_profile_name, radio_tag = remainder.rsplit("__radio", 1)
                radio_index = int(radio_tag)
            except (ValueError, IndexError):
                # Couldn't parse — could be a hand-created RadioProfile that
                # happens to match our prefix. Skip.
                continue
            self.add(
                self.radio_profile(
                    name=orm.name,
                    frequency=orm.frequency,
                    tx_power_min=orm.tx_power_min,
                    tx_power_max=orm.tx_power_max,
                    allowed_channel_list=sorted(orm.allowed_channel_list or []),
                    regulatory_domain=orm.regulatory_domain or "",
                    original_profile_name=original_profile_name,
                    radio_index=radio_index,
                    vdom=self.vdom,
                    hostname=self.hostname,
                )
            )

    def _load_access_points(self) -> None:
        # Scoping for APs is by Device.role — we load all Devices with the
        # configured role + device_type. Without a hostname-side discriminator
        # on Device, this is the best we can do; operators with multiple
        # FortiGates feeding APs of the same model/role into one Nautobot
        # will need to use tenant or location to subscope.
        from nautobot.dcim.models import Device, DeviceType, Location
        from nautobot.extras.models import Role

        if not (self.ap_device_type_model and self.ap_role_name and self.ap_location_name):
            return
        try:
            dt = DeviceType.objects.get(model=self.ap_device_type_model)
            role = Role.objects.get(name=self.ap_role_name)
            location = Location.objects.get(name=self.ap_location_name)
        except (DeviceType.DoesNotExist, Role.DoesNotExist, Location.DoesNotExist) as e:
            if self.job:
                self.job.logger.warning(f"AP sync: scoping ref missing — {e}; skipping AP load")
            return

        for dev in Device.objects.filter(device_type=dt, role=role, location=location):
            self.add(
                self.access_point(
                    serial=dev.serial,
                    name=dev.name,
                    wtp_profile="",  # not represented on the Nautobot side
                    location_name=location.name,
                    device_type_model=dt.model,
                    role_name=role.name,
                    status_name=dev.status.name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                )
            )


def _strip_description_prefix(description: str, original_name: str) -> str:
    """Reverse the ``"<original>: <description>"`` convention from create()."""
    head = f"{original_name}: "
    if description.startswith(head):
        return description[len(head) :]
    if description == original_name:
        return ""
    return description
