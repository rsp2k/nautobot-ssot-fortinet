"""DiffSync model classes — vendor-neutral base + Nautobot-side subclasses."""

from nautobot_ssot_fortinet.diffsync.models.firewall import (
    AddressObject,
    AddressObjectGroup,
    NATPolicy,
    NATPolicyRule,
    Policy,
    PolicyRule,
    ServiceObject,
    ServiceObjectGroup,
)
from nautobot_ssot_fortinet.diffsync.models.nautobot_firewall import (
    NautobotAddressObject,
    NautobotAddressObjectGroup,
    NautobotNATPolicy,
    NautobotNATPolicyRule,
    NautobotPolicy,
    NautobotPolicyRule,
    NautobotServiceObject,
    NautobotServiceObjectGroup,
)
from nautobot_ssot_fortinet.diffsync.models.nautobot_wireless import (
    NautobotAccessPoint,
    NautobotRadioProfile,
    NautobotWirelessNetwork,
)
from nautobot_ssot_fortinet.diffsync.models.wireless import (
    AccessPoint,
    RadioProfile,
    WirelessNetwork,
)

__all__ = [
    "AddressObject",
    "AddressObjectGroup",
    "Policy",
    "PolicyRule",
    "ServiceObject",
    "ServiceObjectGroup",
    "NautobotAddressObject",
    "NautobotAddressObjectGroup",
    "NautobotPolicy",
    "NautobotPolicyRule",
    "NautobotServiceObject",
    "NautobotServiceObjectGroup",
    "NATPolicy",
    "NATPolicyRule",
    "NautobotNATPolicy",
    "NautobotNATPolicyRule",
    "AccessPoint",
    "RadioProfile",
    "WirelessNetwork",
    "NautobotAccessPoint",
    "NautobotRadioProfile",
    "NautobotWirelessNetwork",
]
