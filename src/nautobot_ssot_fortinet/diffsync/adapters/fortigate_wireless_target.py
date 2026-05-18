"""FortiGate-side wireless adapter for the PUSH direction (Nautobot → FortiGate).

Inherits read load() from ``FortiGateWirelessAdapter``; swaps in
write-enabled model subclasses.
"""

from __future__ import annotations

from nautobot_ssot_fortinet.diffsync.adapters.fortigate_wireless import (
    FortiGateWirelessAdapter,
)
from nautobot_ssot_fortinet.diffsync.models.fortigate_target_wireless import (
    FortiGateAccessPoint,
    FortiGateRadioProfile,
    FortiGateWirelessNetwork,
)


class FortiGateWirelessTargetAdapter(FortiGateWirelessAdapter):
    """Read FortiGate wireless state; expose write-enabled models for push."""

    wireless_network = FortiGateWirelessNetwork
    radio_profile = FortiGateRadioProfile
    access_point = FortiGateAccessPoint

    # load() inherited — reads the FortiGate's current wireless state
    # so DiffSync's diff is accurate.
