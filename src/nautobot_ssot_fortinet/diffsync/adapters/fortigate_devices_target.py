"""FortiGate-side DiffSync adapter for the PUSH direction (Nautobot → FortiGate, v3.3+).

Mirrors the pull-side ``FortiGateDevicesAdapter`` — it READS current FortiGate
state the same way so DiffSync can compute accurate diffs. The only thing
that changes is the **model classes**: write-enabled subclasses whose
``create``/``update``/``delete`` methods call the FortiGate REST API.

**Scope (v3.3):** VLAN sub-interface push only. Device records are
read-only on the push side (the FortiGate IS the canonical device
identity). Static routes are read-only in v3.3 — route push will
follow in v3.4 once interface push is field-validated.

In the push Job, this adapter is the **target**; the read-only
``NautobotDevicesAdapter`` is the source. The diff direction is:
"what does Nautobot say should exist on the FortiGate?"
"""

from __future__ import annotations

from nautobot_ssot_fortinet.diffsync.adapters.fortigate_devices import (
    FortiGateDevicesAdapter,
)
from nautobot_ssot_fortinet.diffsync.models.fortigate_target_devices import (
    FortiGateTargetDevice,
    FortiGateTargetInterface,
    FortiGateTargetStaticRoute,
)


class FortiGateDevicesTargetAdapter(FortiGateDevicesAdapter):
    """Read current FortiGate state; expose write-enabled VLAN-interface model.

    Inherits ``load()`` from the parent so we read the full Device +
    Interface + Route state from the device. The push-CRUD logic lives
    on the model classes (see ``fortigate_target_devices.py``); the
    adapter just swaps in the write-enabled subclasses.

    Non-pushable models (Device, StaticRoute in v3.3) inherit base
    DiffSync no-op CRUD, so diff entries for those kinds get applied as
    silent no-ops — they show up in the diff summary but don't trigger
    any FortiOS writes.
    """

    fortigate_device = FortiGateTargetDevice
    fortigate_interface = FortiGateTargetInterface
    fortigate_static_route = FortiGateTargetStaticRoute
