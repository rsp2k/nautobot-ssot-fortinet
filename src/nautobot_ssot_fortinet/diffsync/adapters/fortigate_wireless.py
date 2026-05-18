"""FortiGate-side DiffSync adapter for wireless objects.

Reads ``wireless-controller/{vap,wtp-profile,wtp}`` and emits
WirelessNetwork + RadioProfile (+ optionally AccessPoint) records.

**Mode derivation (one of the trickier mappings):** Nautobot puts ``mode``
on WirelessNetwork; FortiOS puts ``platform-mode`` on WTP-profile.
A WirelessNetwork can be referenced by multiple profiles with different
modes. Strategy: for each VAP, find the WTP-profiles that reference it,
pick the most-common platform-mode among them, fall back to ``"Central"``
if none. Documented as a v1 simplification — operators with
genuinely-mode-split VAPs will see a flattened representation.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from diffsync import Adapter

from nautobot_ssot_fortinet.diffsync.models.wireless import (
    AccessPoint,
    RadioProfile,
    WirelessNetwork,
)
from nautobot_ssot_fortinet.utils.fortios import (
    fortios_band_to_frequency,
    fortios_platform_mode_to_network_mode,
    fortios_security_to_auth,
    mangle_name,
)

if TYPE_CHECKING:
    from fortigate_api import FortiGateAPI


class FortiGateWirelessAdapter(Adapter):
    """Read FortiGate wireless config into DiffSync."""

    wireless_network = WirelessNetwork
    radio_profile = RadioProfile
    access_point = AccessPoint

    top_level = ("wireless_network", "radio_profile", "access_point")

    def __init__(
        self,
        *args,
        client: FortiGateAPI,
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
        """Create the adapter.

        Args:
            client: Configured ``FortiGateAPI`` instance.
            hostname: Used as the first segment of mangled names.
            vdom: VDOM scope (default 'root').
            sync_access_points: If True, also sync ``wireless-controller/wtp``
                entries as Nautobot Device records. Requires ap_device_type,
                ap_role, and ap_location to be provided.
            ap_device_type_model: DeviceType.model string for new APs.
            ap_role_name: Role.name string for new APs.
            ap_location_name: Location.name string for new APs.
            ap_status_name: Status.name (default 'Active').
            job, sync: Standard SSoT plumbing.

        """
        super().__init__(*args, **kwargs)
        self.job = job
        self.sync = sync
        self.client = client
        self.hostname = hostname
        self.vdom = vdom
        self.sync_access_points = sync_access_points
        self.ap_device_type_model = ap_device_type_model
        self.ap_role_name = ap_role_name
        self.ap_location_name = ap_location_name
        self.ap_status_name = ap_status_name

    def load(self) -> None:
        """Pull VAPs, WTP-profiles, and (optionally) WTPs in dependency order."""
        # Load WTP-profiles FIRST — VAP mode derivation reads them. We don't
        # add() them yet; we just buffer the (profile_name → mode) and
        # (profile_name → radio[]) maps for later use.
        raw_profiles = self.client.cmdb.wireless_controller.wtp_profile.get()
        self._radio_profiles_from_wtp_profiles(raw_profiles)

        profile_mode_by_vap = self._build_vap_mode_index(raw_profiles)
        self._load_vaps(profile_mode_by_vap)

        if self.sync_access_points:
            self._load_wtps()

    def _build_vap_mode_index(self, raw_profiles: list[dict]) -> dict[str, str]:
        """For each VAP name, pick the most-common platform-mode across profiles that use it."""
        vap_modes: dict[str, list[str]] = {}
        for prof in raw_profiles:
            platform_mode = prof.get("platform-mode") or "FortiAP-tunnel-mode"
            # WTP-profile VAP list lives at wtp-profile.vaps (some FortiOS
            # versions) or wtp-profile.radio-N.vaps (older format).
            # We check both.
            for vap_entry in prof.get("vaps", []):
                name = vap_entry.get("name")
                if name:
                    vap_modes.setdefault(name, []).append(platform_mode)
            for radio_key in ("radio-1", "radio-2", "radio-3"):
                radio = prof.get(radio_key) or {}
                for vap_entry in radio.get("vaps", []):
                    name = vap_entry.get("name")
                    if name:
                        vap_modes.setdefault(name, []).append(platform_mode)

        return {
            vap_name: fortios_platform_mode_to_network_mode(Counter(modes).most_common(1)[0][0])
            for vap_name, modes in vap_modes.items()
        }

    def _load_vaps(self, vap_mode_index: dict[str, str]) -> None:
        for raw in self.client.cmdb.wireless_controller.vap.get():
            original_name = raw.get("name", "")
            if not original_name:
                continue
            ssid = raw.get("ssid", "") or original_name  # FortiOS often omits ssid when same as name
            mangled = mangle_name(self.hostname, self.vdom, original_name)
            auth, auth_note = fortios_security_to_auth(raw.get("security", "open"))
            mode = vap_mode_index.get(original_name, "Central")

            description_parts: list[str] = []
            if raw.get("comment"):
                description_parts.append(raw["comment"])
            vlanid = raw.get("vlanid")
            if vlanid is not None and vlanid != 0:
                description_parts.append(f"[vlanid={vlanid}]")
            if auth_note:
                description_parts.append(f"[{auth_note}]")
            description = " ".join(description_parts)

            self.add(
                self.wireless_network(
                    name=mangled,
                    ssid=ssid,
                    mode=mode,
                    enabled=(raw.get("status", "enable") == "enable"),
                    authentication=auth,
                    # FortiOS broadcast-ssid: "enable" → visible (hidden=False)
                    hidden=(raw.get("broadcast-ssid", "enable") != "enable"),
                    description=description,
                    original_name=original_name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                )
            )

    def _radio_profiles_from_wtp_profiles(self, raw_profiles: list[dict]) -> None:
        """Fan out: one RadioProfile per (wtp-profile, radio-N) pair."""
        for prof in raw_profiles:
            profile_name = prof.get("name", "")
            if not profile_name:
                continue
            for n in (1, 2, 3):
                radio_key = f"radio-{n}"
                radio = prof.get(radio_key)
                if not radio:
                    continue
                band_raw = radio.get("band") or radio.get("mode") or ""
                frequency = fortios_band_to_frequency(band_raw)
                if frequency is None:
                    if self.job:
                        self.job.logger.warning(
                            f"WTP-profile {profile_name!r} {radio_key} band "
                            f"{band_raw!r} not classifiable — skipping radio"
                        )
                    continue

                allowed_channels: list[int] = []
                raw_channels = radio.get("channel") or []
                # FortiOS channels may be ["1", "6", "11"] or "1 6 11" string.
                if isinstance(raw_channels, str):
                    raw_channels = raw_channels.split()
                for c in raw_channels:
                    try:
                        allowed_channels.append(int(c))
                    except (TypeError, ValueError):
                        continue
                allowed_channels.sort()

                tx_min = _safe_int(radio.get("auto-power-low"))
                tx_max = _safe_int(radio.get("auto-power-high"))

                self.add(
                    self.radio_profile(
                        name=mangle_name(self.hostname, self.vdom, f"{profile_name}__radio{n}"),
                        frequency=frequency,
                        tx_power_min=tx_min,
                        tx_power_max=tx_max,
                        allowed_channel_list=allowed_channels,
                        regulatory_domain=radio.get("country", "") or "",
                        original_profile_name=profile_name,
                        radio_index=n,
                        vdom=self.vdom,
                        hostname=self.hostname,
                    )
                )

    def _load_wtps(self) -> None:
        if not (self.ap_device_type_model and self.ap_role_name and self.ap_location_name):
            if self.job:
                self.job.logger.warning(
                    "sync_access_points=True but ap_device_type/role/location not all "
                    "provided — skipping FortiAP Device sync"
                )
            return
        for raw in self.client.cmdb.wireless_controller.wtp.get():
            serial = raw.get("serial", "") or raw.get("wtp-id", "")
            if not serial:
                continue
            self.add(
                self.access_point(
                    serial=serial,
                    name=raw.get("name", "") or serial,
                    wtp_profile=raw.get("wtp-profile", "") or "",
                    location_name=self.ap_location_name,
                    device_type_model=self.ap_device_type_model,
                    role_name=self.ap_role_name,
                    status_name=self.ap_status_name,
                    vdom=self.vdom,
                    hostname=self.hostname,
                )
            )


def _safe_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
