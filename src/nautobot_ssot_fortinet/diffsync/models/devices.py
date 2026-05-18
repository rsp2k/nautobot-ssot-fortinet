"""DiffSync models for the FortiGate-as-Nautobot-Device pull (v3.0).

Scope: read-only. Map the FortiGate itself to ``dcim.Device`` and its
operator-meaningful system interfaces to ``dcim.Interface`` records,
with IP assignments captured as a list of CIDR strings on each
interface (full first-class IPAddress sync deferred to v3.1).

Mapping conventions:

- **Device.name** = the ExternalIntegration name (e.g. ``"fgt-edge1"``).
  This matches the prefix used throughout the firewall + wireless sync
  for cross-device disambiguation.
- **Device.serial** = the FortiOS serial number (from ``system.global``).
- **Interface.name** = the FortiOS interface name unchanged
  (e.g. ``"wan1"``, ``"internal3"``, ``"lan"``). Interface names are
  unique per-device on FortiOS so no mangling needed.
- **Interface.type** = mapped from FortiOS ``type`` field via
  :func:`fortios_interface_type_to_nautobot` in utils/fortios.py.
- **cidrs** = list of CIDR strings parsed from FortiOS ``ip``/``ip6``
  fields. The Nautobot side creates IPAddress records and assigns them
  to the interface on create/update.

Interface filtering (Phase 1):

- **Synced:** ``physical``, ``hard-switch``, ``switch``, ``aggregate`` —
  the operator-meaningful interfaces with real network presence
- **Skipped:** ``vap-switch`` (already covered by WirelessNetwork sync),
  ``vlan`` quarantine artifacts named ``wqtn.*`` (FortiOS-internal),
  ``tunnel`` (deferred to a VPN-focused release)
"""

from __future__ import annotations

from diffsync import DiffSyncModel


class FortiGateDevice(DiffSyncModel):
    """A FortiGate appliance → Nautobot ``dcim.Device``.

    Identified by ``name`` (the ExternalIntegration's name, matching the
    prefix used throughout the firewall + wireless sync). Serial is an
    attribute so a name-change on the ExternalIntegration doesn't
    detach the Device record.
    """

    _modelname = "fortigate_device"
    _identifiers = ("name",)
    _attributes = (
        "serial",
        "device_type_model",
        "role_name",
        "location_name",
        "status_name",
        "vdom",
    )

    name: str
    serial: str
    device_type_model: str
    role_name: str
    location_name: str
    status_name: str
    vdom: str


class FortiGateInterface(DiffSyncModel):
    """A FortiOS ``system.interface`` → Nautobot ``dcim.Interface``.

    Composite identifier ``(device_name, name)`` because interface names
    are unique per-device but not globally (e.g. every FortiGate has its
    own ``wan1``).
    """

    _modelname = "fortigate_interface"
    _identifiers = ("device_name", "name")
    _attributes = (
        "type",
        "enabled",
        "mtu",
        "description",
        "vdom",
        "cidrs",
    )

    device_name: str
    name: str
    type: str  # Nautobot's dcim.InterfaceType value (e.g. "1000base-t", "lag")
    enabled: bool
    mtu: int | None
    description: str = ""
    vdom: str
    # List of CIDR strings (e.g. ["10.0.0.1/24"]). Empty list for
    # interfaces without IP assignment. The Nautobot side parses these
    # into IPAddress records and assigns them via interface.ip_addresses.
    cidrs: list[str]
