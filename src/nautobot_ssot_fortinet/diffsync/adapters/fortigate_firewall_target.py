"""FortiGate-side DiffSync adapter for the PUSH direction (Nautobot → FortiGate).

Structurally identical to the pull-side ``FortiGateFirewallAdapter`` — it
READS current state from the FortiGate the same way, so DiffSync knows
what's already there and only emits diffs for actual differences. The
only thing that changes is the **model classes**: this adapter uses
write-enabled subclasses whose ``create``/``update``/``delete`` methods
call the FortiGate REST API instead of being no-ops.

In the push Job, this adapter is the **target**; the read-only
``NautobotFirewallAdapter`` is the source.

**Scope (v0.2):** AddressObject (all 4 types), AddressObjectGroup,
ServiceObject, ServiceObjectGroup. Policies and NAT are not yet
push-enabled — those inherit the base classes (DiffSync no-ops) and so
diff entries for them are silently ignored.
"""

from __future__ import annotations

from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall import (
    FortiGateFirewallAdapter,
)
from nautobot_ssot_fortinet.diffsync.models.fortigate_target_firewall import (
    FortiGateAddressObject,
    FortiGateAddressObjectGroup,
    FortiGateNATPolicy,
    FortiGateNATPolicyRule,
    FortiGatePolicy,
    FortiGatePolicyRule,
    FortiGateServiceObject,
    FortiGateServiceObjectGroup,
)


class FortiGateFirewallTargetAdapter(FortiGateFirewallAdapter):
    """Read FortiGate firewall state; expose write-enabled models for push."""

    address_object = FortiGateAddressObject
    address_object_group = FortiGateAddressObjectGroup
    service_object = FortiGateServiceObject
    service_object_group = FortiGateServiceObjectGroup
    policy = FortiGatePolicy
    policy_rule = FortiGatePolicyRule
    nat_policy = FortiGateNATPolicy
    nat_policy_rule = FortiGateNATPolicyRule

    # ``load`` is INHERITED from FortiGateFirewallAdapter — we read the
    # full firewall state from the device so DiffSync sees accurate diffs
    # for every object kind. Push subclasses only exist for the kinds we
    # actually implement; the rest inherit the base read-only models whose
    # create/update/delete are DiffSync no-ops, so non-push kinds produce
    # noise-only diff entries that get applied as silent no-ops.
    #
    # Policies and NAT will get write subclasses in a later iteration —
    # their M2M complexity makes them a separate effort.
