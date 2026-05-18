"""DiffSync models for the FortiGate-as-Nautobot-Device pull (v3.0 + v3.1 VLAN/route extensions).

Scope: read-only. Map the FortiGate itself to ``dcim.Device``, its
operator-meaningful system interfaces (now including VLAN sub-interfaces)
to ``dcim.Interface`` records, and its FortiOS static routes to the
``FortinetStaticRoute`` model introduced in v3.1.

Mapping conventions:

- **Device.name** = the ExternalIntegration name (e.g. ``"fgt-edge1"``).
  Matches the prefix used throughout the firewall + wireless sync for
  cross-device disambiguation.
- **Device.serial** = the FortiOS serial number (from ``system.global``).
- **Interface.name** = the FortiOS interface name unchanged
  (e.g. ``"wan1"``, ``"internal3"``, ``"vlan10"``). Interface names are
  unique per-device on FortiOS so no mangling needed.
- **Interface.type** = mapped from FortiOS ``type`` field via
  :func:`fortios_interface_type_to_nautobot` in utils/fortios.py.
- **cidrs** = list of CIDR strings parsed from FortiOS ``ip``/``ip6``
  fields. The Nautobot side creates IPAddress records and assigns them
  to the interface on create/update.

Interface filtering (v3.1):

- **Synced:** ``physical``, ``hard-switch``, ``switch``, ``aggregate`` —
  operator-meaningful interfaces with real network presence
- **Synced (new in v3.1):** ``vlan`` — operator-defined VLAN sub-interfaces,
  with ``parent_interface`` resolved to a Nautobot Interface FK and the
  VLAN ID stored on the Interface
- **Skipped:** ``vap-switch`` (already covered by WirelessNetwork sync),
  ``tunnel`` (deferred to a VPN-focused release), and any interface whose
  name matches :func:`is_internal_fortios_interface` — currently
  ``wqtn.*`` (VAP quarantine), ``vap.*`` (VAP-tagged switch ports),
  ``ssl.*`` (SSL-VPN tunnel root), ``naf.*``

Static routes (new in v3.1):

- **FortiOS ``router.static``** → ``FortinetStaticRoute`` (Django model
  introduced in v3.1's ``models.py``). Composite identity
  ``(device_name, vdom, seq_num)``.
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

    v3.1 added three VLAN attrs:

    - ``parent_interface_name`` — the FortiOS interface name this VLAN
      rides on (``"internal3"`` for ``"internal3.100"``,
      ``"fortilink"`` for ``"vlan10"``). Resolved to a Nautobot Interface
      FK on the Nautobot side. Empty string for non-VLAN interfaces.
    - ``vlan_id`` — the 802.1Q VLAN ID (1-4094). ``None`` for non-VLAN
      interfaces; required for ``type='virtual'`` VLAN interfaces.
    - ``vlan_mode`` — ``"tagged"`` for trunk-style sub-interfaces (the
      common FortiOS pattern), ``"access"`` for untagged. Defaults to
      ``"tagged"`` since FortiOS VLAN sub-interfaces are 802.1Q-tagged
      by design.
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
        "parent_interface_name",
        "vlan_id",
        "vlan_mode",
    )

    device_name: str
    name: str
    type: str  # Nautobot's dcim.InterfaceType value (e.g. "1000base-t", "lag", "virtual")
    enabled: bool
    mtu: int | None
    description: str = ""
    vdom: str
    # List of CIDR strings (e.g. ["10.0.0.1/24"]). Empty list for
    # interfaces without IP assignment. The Nautobot side parses these
    # into IPAddress records and assigns them via interface.ip_addresses.
    cidrs: list[str]
    # VLAN sub-interface attrs (v3.1+). Empty/None for non-VLAN interfaces.
    parent_interface_name: str = ""
    vlan_id: int | None = None
    vlan_mode: str = ""  # "tagged" | "access" | "" for non-VLAN


class FortiGateStaticRoute(DiffSyncModel):
    """A FortiOS ``router.static`` entry → ``FortinetStaticRoute`` Django model.

    Composite identity ``(device_name, vdom, seq_num)`` — FortiOS uses
    seq_num as the route's primary key per (device, vdom). Mirroring that
    in DiffSync gives us a stable diff key across pulls.

    The ``interface_name`` attr is the **FortiOS** interface name (e.g.
    ``"wan1"``) — the Nautobot side resolves it to an Interface FK via
    ``Device.interfaces.filter(name=...)``. Empty string for blackhole or
    egress-via-routing-table routes.

    ``destination`` is the CIDR string the Nautobot model stores
    verbatim — extracted from FortiOS via
    :func:`fortios_route_destination_cidr` to handle both dotted-mask
    and default-route shapes.
    """

    _modelname = "fortigate_static_route"
    _identifiers = ("device_name", "vdom", "seq_num")
    _attributes = (
        "destination",
        "gateway",
        "interface_name",
        "distance",
        "priority",
        "blackhole",
        "comment",
    )

    device_name: str
    vdom: str
    seq_num: int
    destination: str
    gateway: str = ""  # empty for blackhole
    interface_name: str = ""  # FortiOS interface name; resolved to FK on Nautobot side
    distance: int = 10
    priority: int = 0
    blackhole: bool = False
    comment: str = ""
