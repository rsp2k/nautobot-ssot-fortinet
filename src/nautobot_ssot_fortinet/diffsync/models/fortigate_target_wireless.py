"""FortiGate-side DiffSync subclasses with CRUD — for PUSH (Nautobot → FortiGate wireless).

The inverse of ``nautobot_wireless.py``: writes to the FortiGate REST API
via the adapter's ``client`` attribute.

**Scope (v2.0):**

- ``FortiGateWirelessNetwork`` — full CRUD (VAP create/update/delete).
- ``FortiGateRadioProfile`` — **UPDATE-only**. The parent ``wtp-profile``
  must already exist on the device. The push translates Nautobot
  per-radio attrs back into a partial ``wtp-profile.radio-N`` payload
  via the FortiOS partial-update API.
- ``FortiGateAccessPoint`` — not push-enabled (Devices are typically
  managed via Nautobot's UI directly, not via push sync).

**RadioProfile asymmetry note:** pull fans out one FortiOS wtp-profile
into N Nautobot RadioProfiles (one per radio). Push collapses each
RadioProfile back into a per-radio partial update on the original
wtp-profile. Trying to CREATE a wtp-profile from a single RadioProfile
isn't well-defined (we'd need the full multi-radio + platform-mode
context), so create is intentionally a no-op with a warning.
"""

from __future__ import annotations

from typing import Any

from nautobot_ssot_fortinet.diffsync.models.wireless import (
    AccessPoint,
    RadioProfile,
    WirelessNetwork,
)
from nautobot_ssot_fortinet.utils.fortios import NAME_MANGLE_SEP

# ---- WirelessNetwork (VAP) -----------------------------------------------


# Inverse of FORTIOS_VAP_SECURITY_MAP — Nautobot auth choice → FortiOS
# security string. Multiple FortiOS values map to one Nautobot choice
# (e.g. "wpa-personal" and "wpa2-only-personal" both → "WPA2 Personal");
# on the way back we pick the most-modern / least-ambiguous form.
NAUTOBOT_AUTH_TO_FORTIOS_SECURITY: dict[str, str] = {
    "Open": "open",
    "WPA2 Personal": "wpa2-only-personal",
    "WPA2 Enterprise": "wpa2-only-enterprise",
    "WPA3 SAE": "wpa3-sae",
    "WPA3 Personal": "wpa3-only-personal",
    "WPA3 Enterprise": "wpa3-only-enterprise",
    "WPA3 Enterprise 192Bit": "wpa3-only-enterprise-192",
    "Enhanced Open": "owe",
}


class FortiGateWirelessNetwork(WirelessNetwork):
    """Push WirelessNetwork (VAP) to FortiGate via ``cmdb/wireless-controller/vap``."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        """POST a new VAP to the FortiGate."""
        original_name = _unmangle(ids["name"], adapter.hostname, adapter.vdom)
        payload = _vap_payload(original_name, attrs)
        if payload is None:
            if adapter.job:
                adapter.job.logger.warning(
                    f"Skipping push of {ids['name']!r}: cannot build VAP payload "
                    f"(check authentication={attrs.get('authentication')!r})"
                )
            return super().create(adapter, ids, attrs)
        adapter.client.cmdb.wireless_controller.vap.create(data=payload)
        if adapter.job:
            adapter.job.logger.info(f"  + created VAP on FortiGate: {original_name!r} (SSID {attrs.get('ssid')!r})")
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        """PUT updated VAP fields back to FortiGate."""
        original_name = _unmangle(self.name, self.adapter.hostname, self.adapter.vdom)
        # Always rebuild the full payload — partial updates of
        # security-discriminated fields are fragile.
        merged = {**self.get_attrs(), **attrs}
        payload = _vap_payload(original_name, merged)
        if payload is None:
            return super().update(attrs)
        self.adapter.client.cmdb.wireless_controller.vap.update(uid=original_name, data=payload)
        if self.adapter.job:
            self.adapter.job.logger.info(f"  ~ updated VAP on FortiGate: {original_name!r}")
        return super().update(attrs)

    def delete(self):
        """DELETE the VAP from FortiGate."""
        original_name = _unmangle(self.name, self.adapter.hostname, self.adapter.vdom)
        self.adapter.client.cmdb.wireless_controller.vap.delete(uid=original_name)
        if self.adapter.job:
            self.adapter.job.logger.info(f"  - deleted VAP from FortiGate: {original_name!r}")
        super().delete()
        return self


def _vap_payload(name: str, attrs: dict[str, Any]) -> dict | None:
    """Build a FortiOS ``wireless-controller/vap`` payload from our DiffSync attrs.

    Returns ``None`` for unknown authentication choices.
    """
    security = NAUTOBOT_AUTH_TO_FORTIOS_SECURITY.get(attrs.get("authentication", ""))
    if security is None:
        return None
    payload: dict = {
        "name": name,
        "ssid": attrs.get("ssid", name),
        "security": security,
        "broadcast-ssid": "disable" if attrs.get("hidden") else "enable",
        "status": "enable" if attrs.get("enabled", True) else "disable",
        "comment": (attrs.get("description", "") or "")[:255],
    }
    return payload


# ---- RadioProfile (per-radio update of a wtp-profile) --------------------


class FortiGateRadioProfile(RadioProfile):
    """UPDATE-only push of a per-radio slice of a FortiOS wtp-profile.

    The parent wtp-profile must already exist on the device. ``create()``
    is a no-op with a warning. ``delete()`` is also a no-op (you can't
    delete a single radio from a multi-radio profile; you'd delete the
    whole wtp-profile, which is not modeled here).
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        """No-op — wtp-profile creation isn't supported on push."""
        if adapter.job:
            adapter.job.logger.warning(
                f"Skipping create of RadioProfile {ids['name']!r}: wtp-profile "
                f"creation isn't supported on push. Create the wtp-profile on "
                f"the FortiGate first, then pull to populate Nautobot."
            )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        """PUT a partial wtp-profile update with just the radio-N subfield."""
        merged = {**self.get_attrs(), **attrs}
        profile_name = merged.get("original_profile_name") or self.original_profile_name
        radio_n = merged.get("radio_index") or self.radio_index
        radio_payload = _radio_payload(merged)
        partial_update = {f"radio-{radio_n}": radio_payload}
        self.adapter.client.cmdb.wireless_controller.wtp_profile.update(uid=profile_name, data=partial_update)
        if self.adapter.job:
            self.adapter.job.logger.info(f"  ~ updated wtp-profile {profile_name!r} radio-{radio_n} on FortiGate")
        return super().update(attrs)

    def delete(self):
        """No-op — can't delete a single radio from a multi-radio wtp-profile."""
        if self.adapter.job:
            self.adapter.job.logger.warning(
                f"Skipping delete of RadioProfile {self.name!r}: can't delete a single "
                f"radio. Delete the entire wtp-profile on the FortiGate UI if needed."
            )
        super().delete()
        return self


def _radio_payload(attrs: dict[str, Any]) -> dict:
    """Build a FortiOS ``wtp-profile.radio-N`` partial payload from DiffSync attrs."""
    # Reverse of fortios_band_to_frequency — we picked the simplest band
    # string per frequency since FortiOS accepts several aliases. Operators
    # who use specific 802.11 profile bands (e.g. "802.11ax-5G" vs
    # "802.11ac") should set them on the FortiGate; updates from our side
    # preserve the channel/power settings.
    freq_to_band = {
        "2.4GHz": "802.11n,g-only",
        "5GHz": "802.11ax-5G",
        "6GHz": "802.11ax-6G",
    }
    payload: dict = {}
    if attrs.get("frequency"):
        band = freq_to_band.get(attrs["frequency"])
        if band:
            payload["band"] = band
    if attrs.get("tx_power_min") is not None:
        payload["auto-power-low"] = attrs["tx_power_min"]
    if attrs.get("tx_power_max") is not None:
        payload["auto-power-high"] = attrs["tx_power_max"]
    channels = attrs.get("allowed_channel_list")
    if channels is not None:
        payload["channel"] = [str(c) for c in channels]
    if attrs.get("regulatory_domain"):
        payload["country"] = attrs["regulatory_domain"]
    return payload


# ---- AccessPoint (not push-enabled in v2.0) ------------------------------


class FortiGateAccessPoint(AccessPoint):
    """AP push is a no-op for now — Devices are typically managed via Nautobot UI."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        if adapter.job:
            adapter.job.logger.warning(f"AccessPoint push not implemented; skipping {ids['serial']!r}")
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        return super().update(attrs)

    def delete(self):
        return super().delete()


def _unmangle(mangled: str, hostname: str, vdom: str) -> str:
    """Strip ``<hostname>__<vdom>__`` to recover the original FortiOS name."""
    prefix = f"{hostname}{NAME_MANGLE_SEP}{vdom}{NAME_MANGLE_SEP}"
    return mangled[len(prefix) :] if mangled.startswith(prefix) else mangled
