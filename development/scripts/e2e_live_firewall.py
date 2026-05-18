"""Live firewall sync against the configured ExternalIntegration `fgt-dev`.

Same diff/idempotency check pattern as ``e2e_firewall_sync.py``, but uses a
**real** FortiGateAPI client (built via ``build_client(ext)``) instead of
the MagicMock-fixture-fed one. This is the truest test of the integration
because it exercises the real FortiOS REST shapes — fixture-only tests
miss vendor-specific field quirks (e.g. FortiOS ``interface-subnet``
address type, or non-standard ``protocol-number`` values).

Run via:
    make -C development e2e-live-firewall

Or directly:
    docker compose exec nautobot-web nautobot-server shell_plus --quiet-load \\
      --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_live_firewall.py').read()); run()"
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"


def _build_adapters():
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall import (
        FortiGateFirewallAdapter,
    )
    from nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall import (
        NautobotFirewallAdapter,
    )

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    # `with` keeps a single admin session across all endpoint queries —
    # avoids the per-request login storm in user/pass auth mode.
    with build_client(ext) as client:
        src = FortiGateFirewallAdapter(client=client, hostname=ext.name, vdom=VDOM)
        src.load()

    tgt = NautobotFirewallAdapter(hostname=ext.name, vdom=VDOM)
    tgt.load()
    return src, tgt, ext


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


def _wipe(ext_name: str) -> None:
    from nautobot_firewall_models.models import (
        AddressObject,
        AddressObjectGroup,
        NATPolicy,
        Policy,
        ServiceObjectGroup,
    )

    prefix = f"{ext_name}__{VDOM}__"
    nat_n = 0
    for nat_policy in NATPolicy.objects.filter(name__startswith=prefix):
        rules = list(nat_policy.nat_policy_rules.all())
        nat_policy.nat_policy_rules.clear()
        for r in rules:
            r.delete()
            nat_n += 1
    print(f"  cleaned NATPolicyRules: {nat_n}")
    print(f"  cleaned NATPolicies: {NATPolicy.objects.filter(name__startswith=prefix).delete()}")

    rule_n = 0
    for policy in Policy.objects.filter(name__startswith=prefix):
        rules = list(policy.policy_rules.all())
        policy.policy_rules.clear()
        for r in rules:
            r.delete()
            rule_n += 1
    print(f"  cleaned PolicyRules: {rule_n}")
    print(f"  cleaned Policies: {Policy.objects.filter(name__startswith=prefix).delete()}")
    print(f"  cleaned ServiceObjectGroups: {ServiceObjectGroup.objects.filter(name__startswith=prefix).delete()}")
    print(f"  cleaned AddressObjectGroups: {AddressObjectGroup.objects.filter(name__startswith=prefix).delete()}")
    print(f"  cleaned AddressObjects: {AddressObject.objects.filter(name__startswith=prefix).delete()}")


def run() -> None:
    print("=" * 70)
    print(f"LIVE firewall sync — ext={EXT_NAME!r}, vdom={VDOM!r}")
    print("=" * 70)

    print("\n[0/4] Wipe prior runs for deterministic start:")
    _wipe(EXT_NAME)

    print("\n[1/4] Adapter loads (FortiGate REST + Nautobot ORM):")
    src, tgt, ext = _build_adapters()
    _summarize(src, tgt)

    print("\n[2/4] First sync — should be all creates:")
    diff = tgt.diff_from(src)
    print(f"  diff summary: {diff.summary()}")
    try:
        tgt.sync_from(src)
        print("  sync_from() complete")
    except Exception as e:
        import traceback

        print(f"  SYNC FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()
        return

    print("\n[3/4] Idempotency — re-pull both sides, expect zero diff:")
    src2, tgt2, _ = _build_adapters()
    diff2 = tgt2.diff_from(src2)
    summary = diff2.summary()
    print(f"  second diff: {summary}")
    has_changes = any(v for k, v in summary.items() if k != "no-change" and v)
    if has_changes:
        print("  FAIL: second sync produced non-empty diff — NOT idempotent")
        # Print exactly what's different so we can debug.
        print("  Differing items:")
        for child in diff2.get_children():
            for item in child.get_children():
                if item.action and item.action != "no-change":
                    print(f"    {item.action} {item.type} {item.name}  -  {item.get_attrs_diffs()}")
    else:
        print("  PASS: zero diffs on re-sync — fully idempotent against the live FortiGate")
    print("=" * 70)
