"""End-to-end firewall sync against the dev DB, fed by fixture data.

This is a manual/dev test, not a pytest. It:
  1. Mocks a fortigate-api client whose .get() methods return our test fixtures
  2. Constructs the FortiGate adapter and the Nautobot adapter
  3. Runs both load() methods, calculates the diff, applies the sync
  4. Verifies the records landed in nautobot-firewall-models tables
  5. Re-runs sync — idempotency check (second diff must be empty)

Run via:
    docker compose exec nautobot-web nautobot-server shell_plus --quiet-load \\
      --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_firewall_sync.py').read()); run()"

Or via the Makefile target ``make -C development e2e-firewall``.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

HOSTNAME = "fgt-dev"
VDOM = "root"
FIXTURES = Path("/opt/plugin/tests/fixtures")


def _make_client() -> MagicMock:
    client = MagicMock()
    client.cmdb.firewall.address.get.return_value = json.loads(
        (FIXTURES / "firewall_address.json").read_text()
    )
    client.cmdb.firewall.addrgrp.get.return_value = json.loads(
        (FIXTURES / "firewall_addrgrp.json").read_text()
    )
    client.cmdb.firewall_service.custom.get.return_value = json.loads(
        (FIXTURES / "firewall_service_custom.json").read_text()
    )
    client.cmdb.firewall_service.group.get.return_value = json.loads(
        (FIXTURES / "firewall_service_group.json").read_text()
    )
    client.cmdb.firewall.policy.get.return_value = json.loads(
        (FIXTURES / "firewall_policy.json").read_text()
    )
    client.cmdb.firewall.vip.get.return_value = json.loads(
        (FIXTURES / "firewall_vip.json").read_text()
    )
    return client


def _build_adapters():
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall import (
        FortiGateFirewallAdapter,
    )
    from nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall import (
        NautobotFirewallAdapter,
    )

    src = FortiGateFirewallAdapter(client=_make_client(), hostname=HOSTNAME, vdom=VDOM)
    src.load()

    tgt = NautobotFirewallAdapter(hostname=HOSTNAME, vdom=VDOM)
    tgt.load()
    return src, tgt


def _summarize(src, tgt) -> None:
    for kind in (
        "address_object",
        "address_object_group",
        "service_object",
        "service_object_group",
        "policy",
        "policy_rule",
        "nat_policy",
        "nat_policy_rule",
    ):
        s = len(src.get_all(kind))
        t = len(tgt.get_all(kind))
        print(f"  {kind:25} source={s:3d}  target={t:3d}")


def _orm_counts() -> dict[str, int]:
    from nautobot_firewall_models.models import (
        AddressObject,
        AddressObjectGroup,
        NATPolicy,
        Policy,
        ServiceObject,
        ServiceObjectGroup,
    )

    prefix = f"{HOSTNAME}__{VDOM}__"
    return {
        "AddressObject": AddressObject.objects.filter(name__startswith=prefix).count(),
        "AddressObjectGroup": AddressObjectGroup.objects.filter(name__startswith=prefix).count(),
        "ServiceObject (all)": ServiceObject.objects.count(),
        "ServiceObjectGroup": ServiceObjectGroup.objects.filter(name__startswith=prefix).count(),
        "Policy": Policy.objects.filter(name__startswith=prefix).count(),
        "PolicyRule (linked)": sum(
            p.policy_rules.count()
            for p in Policy.objects.filter(name__startswith=prefix)
        ),
        "NATPolicy": NATPolicy.objects.filter(name__startswith=prefix).count(),
        "NATPolicyRule (linked)": sum(
            np.nat_policy_rules.count()
            for np in NATPolicy.objects.filter(name__startswith=prefix)
        ),
    }


def _wipe_scoped() -> None:
    """Clean prefix-matched records before the test to guarantee a known starting state."""
    from nautobot_firewall_models.models import (
        AddressObject,
        AddressObjectGroup,
        Policy,
        PolicyRule,
        ServiceObjectGroup,
    )

    prefix = f"{HOSTNAME}__{VDOM}__"
    # firewall-models has a protect_on_delete signal that refuses to delete
    # PolicyRules + NATPolicyRules still attached to a (NAT)Policy. Unlink
    # via the M2M *first*, then delete rules, then delete (NAT)Policies.
    from nautobot_firewall_models.models import NATPolicy

    nat_rule_count = 0
    for nat_policy in NATPolicy.objects.filter(name__startswith=prefix):
        rules = list(nat_policy.nat_policy_rules.all())
        nat_policy.nat_policy_rules.clear()
        for rule in rules:
            rule.delete()
            nat_rule_count += 1
    print(f"  cleaned NATPolicyRules: {nat_rule_count}")
    deleted = NATPolicy.objects.filter(name__startswith=prefix).delete()
    print(f"  cleaned NATPolicies: {deleted}")

    rule_count = 0
    for policy in Policy.objects.filter(name__startswith=prefix):
        rules = list(policy.policy_rules.all())
        policy.policy_rules.clear()
        for rule in rules:
            rule.delete()
            rule_count += 1
    print(f"  cleaned PolicyRules: {rule_count}")
    deleted = Policy.objects.filter(name__startswith=prefix).delete()
    print(f"  cleaned Policies: {deleted}")
    deleted = ServiceObjectGroup.objects.filter(name__startswith=prefix).delete()
    print(f"  cleaned ServiceObjectGroups: {deleted}")
    deleted = AddressObjectGroup.objects.filter(name__startswith=prefix).delete()
    print(f"  cleaned AddressObjectGroups: {deleted}")
    deleted = AddressObject.objects.filter(name__startswith=prefix).delete()
    print(f"  cleaned AddressObjects: {deleted}")


def run() -> None:
    print("=" * 70)
    print(f"E2E firewall sync — hostname={HOSTNAME!r}, vdom={VDOM!r}")
    print("=" * 70)

    print("\n[0/4] Cleaning prefix-scoped records for deterministic start:")
    _wipe_scoped()

    print("\n[1/4] Initial adapter loads:")
    src, tgt = _build_adapters()
    _summarize(src, tgt)

    print("\n[2/4] First sync — should be all creates:")
    diff = tgt.diff_from(src)
    print(f"  diff summary: {diff.summary()}")
    tgt.sync_from(src)
    print("  sync_from() complete")

    print("\n[3/4] ORM record counts after first sync:")
    for label, n in _orm_counts().items():
        print(f"  {label:25} = {n}")

    print("\n[4/4] Idempotency check — re-load + diff, expect no changes:")
    # Fresh adapter instances so we don't reuse stale DiffSync state.
    src2, tgt2 = _build_adapters()
    diff2 = tgt2.diff_from(src2)
    summary = diff2.summary()
    print(f"  second diff: {summary}")
    has_changes = any(v for k, v in summary.items() if k != "no-change" and v)
    if has_changes:
        print("  FAIL: second sync produced a non-empty diff — sync is NOT idempotent")
    else:
        print("  PASS: zero diffs on re-sync — fully idempotent")
    print("=" * 70)
