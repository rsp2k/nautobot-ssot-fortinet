"""DiffSync model classes for FortiGate wireless objects.

Maps FortiOS ``wireless-controller/{vap,wtp-profile,wtp}`` endpoints to
Nautobot core's ``WirelessNetwork`` + ``RadioProfile`` (+ optionally
``Device`` for managed FortiAPs). One ``WirelessNetwork`` per VAP; one
``RadioProfile`` per (WTP-profile, radio-index) pair.

**Identifier strategy (mangled per usual):**
- ``WirelessNetwork``: ``<hostname>__<vdom>__<vap-name>``
- ``RadioProfile``: ``<hostname>__<vdom>__<wtp-profile-name>__radio<N>``
- ``AccessPoint`` (optional): the FortiAP serial number is the natural key
  (no mangling needed — serials are globally unique across vendors).
"""

from __future__ import annotations

from diffsync import DiffSyncModel


class WirelessNetwork(DiffSyncModel):
    """A FortiGate ``wireless-controller/vap`` → Nautobot ``WirelessNetwork``.

    The ``mode`` attribute lives on the WirelessNetwork in Nautobot but on
    the WTP-profile in FortiOS. We pick the mode from the first WTP-profile
    that references this VAP, with a default of ``"Central"`` when no
    profile references it. Documented in the FortiGate adapter.
    """

    _modelname = "wireless_network"
    _identifiers = ("name",)
    _attributes = (
        "ssid",
        "mode",
        "enabled",
        "authentication",
        "hidden",
        "description",
        "original_name",
        "vdom",
        "hostname",
    )

    name: str
    ssid: str
    mode: str
    enabled: bool
    authentication: str
    hidden: bool
    description: str = ""
    original_name: str
    vdom: str
    hostname: str


class RadioProfile(DiffSyncModel):
    """A FortiGate WTP-profile radio → Nautobot ``RadioProfile``.

    Each WTP-profile in FortiOS bundles 1–3 radios (radio-1, radio-2, radio-3
    on tri-band APs). We fan out: one RadioProfile per (profile, radio-index).

    ``frequency`` uses Nautobot ``RadioProfileFrequencyChoices`` value form:
    ``"2.4GHz"``, ``"5GHz"``, ``"6GHz"`` (no space).
    """

    _modelname = "radio_profile"
    _identifiers = ("name",)
    _attributes = (
        "frequency",
        "tx_power_min",
        "tx_power_max",
        "allowed_channel_list",
        "regulatory_domain",
        "original_profile_name",
        "radio_index",
        "vdom",
        "hostname",
    )

    name: str
    frequency: str
    tx_power_min: int | None
    tx_power_max: int | None
    allowed_channel_list: list[int]
    regulatory_domain: str
    original_profile_name: str
    radio_index: int
    vdom: str
    hostname: str


class AccessPoint(DiffSyncModel):
    """A FortiGate ``wireless-controller/wtp`` → Nautobot ``Device`` (role=AP).

    Only synced when the Job is configured with ``ap_device_type``,
    ``ap_role``, and ``ap_location`` ObjectVars — without those, FortiAPs
    are skipped (all-in-one devices like the FWF-61E typically have no
    managed WTPs anyway). Identifier is the WTP serial, which is globally
    unique across FortiAP units.
    """

    _modelname = "access_point"
    _identifiers = ("serial",)
    _attributes = (
        "name",
        "wtp_profile",
        "location_name",
        "device_type_model",
        "role_name",
        "status_name",
        "vdom",
        "hostname",
    )

    serial: str
    name: str
    wtp_profile: str
    location_name: str
    device_type_model: str
    role_name: str
    status_name: str
    vdom: str
    hostname: str
