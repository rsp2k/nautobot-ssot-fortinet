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
from nautobot_ssot_fortinet.utils.fortios import NAME_MANGLE_SEP, check_fortios_response

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
        check_fortios_response(
            adapter.client.cmdb.wireless_controller.vap.create(data=payload),
            label=f"vap.create {original_name!r}",
        )
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
        check_fortios_response(
            self.adapter.client.cmdb.wireless_controller.vap.update(data=payload),
            label=f"vap.update {original_name!r}",
        )
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
    """Push of a per-radio slice of a FortiOS wtp-profile.

    Two paths depending on whether the parent wtp-profile already exists
    on the target FortiGate:

    1. **wtp-profile exists on target** — partial update only (``radio-N``
       payload sent to the existing profile). This was the v2.0 behavior.

    2. **wtp-profile doesn't exist on target** (v2.2+) — sibling
       aggregation: the first sibling create() call collects all
       RadioProfiles for the same ``original_profile_name`` from the
       SOURCE adapter's store, builds a combined wtp-profile payload
       with all radios populated, and POSTs the whole profile. Later
       sibling create() calls detect the wtp-profile is now in the
       target store and become no-ops.

    Requires the Job to stash ``source_adapter`` on the target adapter
    before ``execute_sync()`` runs.

    ``delete()`` is still a no-op (can't delete a single radio from a
    multi-radio profile; delete the whole wtp-profile on the FortiGate UI).
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        """Per-radio update if the wtp-profile exists, else aggregated create."""
        profile_name = attrs.get("original_profile_name")
        if not profile_name:
            if adapter.job:
                adapter.job.logger.warning(f"Skipping RadioProfile {ids['name']!r}: no original_profile_name")
            return super().create(adapter, ids, attrs)

        # If the wtp-profile already exists on the target (i.e. there's
        # another sibling RadioProfile in the target store with the same
        # original_profile_name), this is a normal per-radio update.
        # Otherwise, we need to aggregate siblings from the SOURCE side
        # and create the whole wtp-profile at once.
        existing_target_siblings = [
            rp for rp in adapter.get_all(cls) if rp.original_profile_name == profile_name and rp.name != ids["name"]
        ]

        if existing_target_siblings:
            # Parent exists — partial radio-N update.
            radio_n = attrs.get("radio_index")
            radio_payload = _radio_payload(attrs)
            # fortigate-api Connector.update() reads the uid (here: name)
            # from inside data, not as a kwarg.
            partial_update = {"name": profile_name, f"radio-{radio_n}": radio_payload}
            check_fortios_response(
                adapter.client.cmdb.wireless_controller.wtp_profile.update(data=partial_update),
                label=f"wtp_profile.update {profile_name!r} radio-{radio_n}",
            )
            if adapter.job:
                adapter.job.logger.info(f"  ~ added radio-{radio_n} to existing wtp-profile {profile_name!r}")
            return super().create(adapter, ids, attrs)

        # Parent doesn't exist — sibling aggregation.
        source = getattr(adapter, "source_adapter", None)
        if source is None:
            if adapter.job:
                adapter.job.logger.warning(
                    f"Skipping RadioProfile {ids['name']!r}: wtp-profile {profile_name!r} "
                    f"doesn't exist on FortiGate and source_adapter isn't accessible "
                    f"for sibling aggregation. Create the wtp-profile on the FortiGate "
                    f"UI first, then re-run push."
                )
            return super().create(adapter, ids, attrs)

        sibling_source_rps = [rp for rp in source.get_all(cls) if rp.original_profile_name == profile_name]
        if not sibling_source_rps:
            return super().create(adapter, ids, attrs)

        payload: dict[str, Any] = {
            "name": profile_name,
            # Default platform-mode for managed FortiAPs. Operators with
            # different deployments (mesh, bridge, local-flex) should
            # override on the FortiGate UI after create — the wtp-profile
            # platform-mode isn't a RadioProfile attr since it lives on
            # the container, not individual radios.
            "platform-mode": "FortiAP-tunnel-mode",
            # FortiOS rejects parentheses in comments as XSS vulnerability
            # characters (error -173). Using brackets keeps it readable while
            # passing the check.
            "comment": f"Created from Nautobot via nautobot-ssot-fortinet sync [{len(sibling_source_rps)} radios]",
        }
        for sib in sibling_source_rps:
            payload[f"radio-{sib.radio_index}"] = _radio_payload(
                {
                    "frequency": sib.frequency,
                    "tx_power_min": sib.tx_power_min,
                    "tx_power_max": sib.tx_power_max,
                    "allowed_channel_list": sib.allowed_channel_list,
                    "regulatory_domain": sib.regulatory_domain,
                }
            )

        check_fortios_response(
            adapter.client.cmdb.wireless_controller.wtp_profile.create(data=payload),
            label=f"wtp_profile.create {profile_name!r}",
        )
        if adapter.job:
            adapter.job.logger.info(
                f"  + created wtp-profile {profile_name!r} on FortiGate ({len(sibling_source_rps)} radios)"
            )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        """PUT a partial wtp-profile update with just the radio-N subfield."""
        merged = {**self.get_attrs(), **attrs}
        profile_name = merged.get("original_profile_name") or self.original_profile_name
        radio_n = merged.get("radio_index") or self.radio_index
        radio_payload = _radio_payload(merged)
        # fortigate-api Connector.update() reads the uid (here: name) from
        # inside data, not as a kwarg.
        partial_update = {"name": profile_name, f"radio-{radio_n}": radio_payload}
        check_fortios_response(
            self.adapter.client.cmdb.wireless_controller.wtp_profile.update(data=partial_update),
            label=f"wtp_profile.update {profile_name!r} radio-{radio_n}",
        )
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
        # FortiOS wtp-profile.radio-N.channel expects a list of {"chan": str}
        # objects, NOT a flat list of strings/ints. Probed empirically against
        # FortiOS v7.0.14 — flat lists return HTTP 500 with error=-1 silently
        # (status_code wasn't checked pre-v2.4 so this masked for 3 releases).
        payload["channel"] = [{"chan": str(c)} for c in channels]
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
