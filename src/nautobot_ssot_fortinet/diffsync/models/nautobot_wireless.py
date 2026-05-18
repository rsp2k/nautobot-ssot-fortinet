"""Nautobot-side DiffSync subclasses with CRUD for wireless models.

Same pattern as ``nautobot_firewall.py``: ORM imports inside methods so
unit tests can import the module without a fully-bootstrapped Django.
"""

from __future__ import annotations

from typing import Any

from nautobot_ssot_fortinet.diffsync.models.wireless import (
    AccessPoint,
    RadioProfile,
    WirelessNetwork,
)


class NautobotWirelessNetwork(WirelessNetwork):
    """Nautobot WirelessNetwork — scalar attrs only, no FK resolution required."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.wireless.models import WirelessNetwork as ORMWirelessNetwork

        ORMWirelessNetwork.objects.update_or_create(
            name=ids["name"],
            defaults={
                "ssid": attrs["ssid"],
                "mode": attrs["mode"],
                "enabled": attrs["enabled"],
                "authentication": attrs["authentication"],
                "hidden": attrs["hidden"],
                "description": _compose_description(attrs),
            },
        )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot.wireless.models import WirelessNetwork as ORMWirelessNetwork

        try:
            obj = ORMWirelessNetwork.objects.get(name=self.name)
        except ORMWirelessNetwork.DoesNotExist:
            return super().update(attrs)
        for field in ("ssid", "mode", "enabled", "authentication", "hidden"):
            if field in attrs:
                setattr(obj, field, attrs[field])
        if "description" in attrs or "original_name" in attrs:
            merged = {**self.get_attrs(), **attrs}
            obj.description = _compose_description(merged)
        obj.save()
        return super().update(attrs)

    def delete(self):
        from nautobot.wireless.models import WirelessNetwork as ORMWirelessNetwork

        ORMWirelessNetwork.objects.filter(name=self.name).delete()
        super().delete()
        return self


class NautobotRadioProfile(RadioProfile):
    """Nautobot RadioProfile — frequency + tx power + channel list."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.wireless.models import RadioProfile as ORMRadioProfile

        ORMRadioProfile.objects.update_or_create(
            name=ids["name"],
            defaults={
                "frequency": attrs["frequency"],
                "tx_power_min": attrs.get("tx_power_min"),
                "tx_power_max": attrs.get("tx_power_max"),
                "allowed_channel_list": attrs.get("allowed_channel_list") or [],
                "regulatory_domain": attrs.get("regulatory_domain", "") or "",
            },
        )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot.wireless.models import RadioProfile as ORMRadioProfile

        try:
            obj = ORMRadioProfile.objects.get(name=self.name)
        except ORMRadioProfile.DoesNotExist:
            return super().update(attrs)
        for field in (
            "frequency",
            "tx_power_min",
            "tx_power_max",
            "allowed_channel_list",
            "regulatory_domain",
        ):
            if field in attrs:
                setattr(
                    obj,
                    field,
                    attrs[field]
                    if attrs[field] is not None
                    else ("" if field == "regulatory_domain" else [] if field == "allowed_channel_list" else None),
                )
        obj.save()
        return super().update(attrs)

    def delete(self):
        from nautobot.wireless.models import RadioProfile as ORMRadioProfile

        ORMRadioProfile.objects.filter(name=self.name).delete()
        super().delete()
        return self


class NautobotAccessPoint(AccessPoint):
    """Nautobot Device (role=AP) — FK resolution by name (device_type, role, location, status)."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.dcim.models import Device, DeviceType, Location
        from nautobot.extras.models import Role, Status

        Device.objects.update_or_create(
            serial=ids["serial"],
            defaults={
                "name": attrs["name"],
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
            dev = Device.objects.get(serial=self.serial)
        except Device.DoesNotExist:
            return super().update(attrs)
        if "name" in attrs:
            dev.name = attrs["name"]
        dev.save()
        return super().update(attrs)

    def delete(self):
        from nautobot.dcim.models import Device

        Device.objects.filter(serial=self.serial).delete()
        super().delete()
        return self


def _compose_description(attrs: dict[str, Any]) -> str:
    """Build the WirelessNetwork.description, preserving original_name for round-trip."""
    orig = attrs.get("original_name") or ""
    free = attrs.get("description") or ""
    if orig and free:
        return f"{orig}: {free}"
    if orig:
        return orig
    return free
