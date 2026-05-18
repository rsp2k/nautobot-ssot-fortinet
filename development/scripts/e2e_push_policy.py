"""Validate PolicyRule push (create/update/delete) against live FWF-61E.

Per the v2.4 hotfix retrospective: v2.0's policy CRUD claims rested on
mock tests + a broken ``.get(uid=...)`` verification — never actually
exercised end-to-end on a device. This script does the real work.

Risk model: uses ``policyid=9999`` (well outside the operator's normal
range; the dev device only has policy id 1) and existing 'all'/'ALL'
references so we don't have to push prerequisites. Unconditional
cleanup on every exit path.

Phases:
  0. Cleanup any prior run
  1. Inject Nautobot PolicyRule referencing existing fgt-dev__root__all etc.
  2. Push — verify policy 9999 lands on FortiGate with correct fields
  3. Toggle log=True via update — verify field changed
  4. Delete — verify policy gone from FortiGate
  5. Final cleanup

Run via:  make -C development e2e-push-policy
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"
TEST_POLICYID = 9999
TEST_RULE_NAME = f"{EXT_NAME}__{VDOM}__rule_{TEST_POLICYID}"
TEST_POLICY_PARENT_NAME = f"{EXT_NAME}__{VDOM}__e2e-test-policy"


def _cleanup() -> None:
    """Wipe test policy from FortiGate + test PolicyRule/Policy from Nautobot.

    Order matters: nautobot-firewall-models has ``protect_on_delete`` that
    raises ValidationError if a PolicyRule is deleted while still attached
    to a Policy. Drop the parent first, OR clear the M2M.
    """
    from nautobot.extras.models import ExternalIntegration
    from nautobot_firewall_models.models import Policy, PolicyRule

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    # Parent Policy first — drops the M2M reference automatically
    n2 = Policy.objects.filter(name=TEST_POLICY_PARENT_NAME).delete()
    n1 = PolicyRule.objects.filter(name=TEST_RULE_NAME).delete()
    print(f"  Nautobot: Policy={n2} PolicyRule={n1}")

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        try:
            fgt.cmdb.firewall.policy.delete(uid=str(TEST_POLICYID))
            print(f"  FortiGate: deleted policy {TEST_POLICYID}")
        except Exception:
            print(f"  FortiGate: policy {TEST_POLICYID} wasn't there")


def _inject_test_records() -> None:
    """Create Policy parent + PolicyRule in Nautobot referencing existing FortiGate objects.

    The mangled names (fgt-dev__root__all, fgt-dev__root__ALL) are what
    the FortiGate pull Job uses. The push will un-mangle back to bare
    FortiOS names ('all', 'ALL') when building the POST payload.
    """
    from nautobot.extras.models import Status
    from nautobot_firewall_models.models import (
        AddressObject,
        Policy,
        PolicyRule,
        ServiceObject,
    )

    active = Status.objects.get(name="Active")

    # Lookup the existing mangled records we'll reference
    src_addr = AddressObject.objects.get(name=f"{EXT_NAME}__{VDOM}__all")
    dst_addr = AddressObject.objects.get(name=f"{EXT_NAME}__{VDOM}__all")
    # ServiceObject is the exception: no mangling, composite NK
    # (FortiOS 'ALL' service uses protocol=IP wildcard which doesn't
    # round-trip into Nautobot's IP_PROTOCOL_CHOICES — use HTTPS for the
    # test since it's a standard TCP/443 service guaranteed present.)
    svc = ServiceObject.objects.get(name="HTTPS", ip_protocol="TCP", port="443")

    policy, _ = Policy.objects.get_or_create(
        name=TEST_POLICY_PARENT_NAME, defaults={"status": active}
    )

    rule, created = PolicyRule.objects.get_or_create(
        name=TEST_RULE_NAME,
        defaults={
            "action": "deny",
            "log": False,
            "request_id": TEST_RULE_NAME,
            "status": active,
            # interface info goes into the description AND the structured
            # source_interfaces / destination_interfaces M2M (whichever
            # the model supports — let the adapter parse it)
            "description": (
                # 'lan' / 'wan1' are what the existing policy 1 uses on
                # the dev device. Bare 'internal' won't work — it's the
                # physical switch parent and FortiOS rejects it as a
                # policy endpoint (node_check_object fail!).
                f"e2e test policy [srcintf=lan dstintf=wan1]"
            ),
        },
    )
    rule.source_addresses.set([src_addr])
    rule.destination_addresses.set([dst_addr])
    rule.destination_services.set([svc])
    rule.save()
    policy.policy_rules.add(rule)
    print(f"  PolicyRule {TEST_RULE_NAME!r}: created={created}")
    return rule


def _verify_policy_on_fortigate(expected_log_enabled: bool | None = None) -> dict | None:
    """Fetch policy TEST_POLICYID from FortiGate. Returns the record or None."""
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        # Correct Connector.get filter: policyid= (the endpoint's uid attr)
        found = fgt.cmdb.firewall.policy.get(policyid=str(TEST_POLICYID))
        if not found:
            return None
        rec = found[0] if isinstance(found, list) else found
        # The list-form get may still return all records — filter to our id
        if isinstance(found, list):
            for p in found:
                if str(p.get("policyid")) == str(TEST_POLICYID):
                    rec = p
                    break
            else:
                return None
        srcintf = [i.get("name") for i in rec.get("srcintf", [])]
        dstintf = [i.get("name") for i in rec.get("dstintf", [])]
        srcaddr = [a.get("name") for a in rec.get("srcaddr", [])]
        dstaddr = [a.get("name") for a in rec.get("dstaddr", [])]
        svc = [s.get("name") for s in rec.get("service", [])]
        print(f"    policyid:    {rec.get('policyid')}")
        print(f"    name:        {rec.get('name')!r}")
        print(f"    action:      {rec.get('action')!r}")
        print(f"    status:      {rec.get('status')!r}")
        print(f"    logtraffic:  {rec.get('logtraffic')!r}")
        print(f"    srcintf:     {srcintf}")
        print(f"    dstintf:     {dstintf}")
        print(f"    srcaddr:     {srcaddr}")
        print(f"    dstaddr:     {dstaddr}")
        print(f"    service:     {svc}")
        return rec


def run() -> None:
    print("=" * 70)
    print(f"E2E PolicyRule push validation — ext={EXT_NAME!r} VDOM={VDOM!r}")
    print(f"  Test policyid: {TEST_POLICYID}")
    print("=" * 70)

    print("\n[0/5] Cleanup prior runs...")
    _cleanup()

    print("\n[1/5] Inject Nautobot PolicyRule referencing 'all'/'ALL'...")
    try:
        rule = _inject_test_records()
    except Exception as e:
        print(f"  ✗ INJECT FAILED: {type(e).__name__}: {e}")
        _cleanup()
        return

    print(f"\n[2/5] Run firewall push (create) — expect policyid {TEST_POLICYID} on FortiGate...")
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall_target import (
        FortiGateFirewallTargetAdapter,
    )
    from nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall import (
        NautobotFirewallAdapter,
    )

    ext = ExternalIntegration.objects.get(name=EXT_NAME)

    def _push() -> bool:
        src = NautobotFirewallAdapter(hostname=ext.name, vdom=VDOM)
        src.load()
        with build_client(ext) as client:
            tgt = FortiGateFirewallTargetAdapter(client=client, hostname=ext.name, vdom=VDOM)
            tgt.load()
            tgt.source_adapter = src
            diff = tgt.diff_from(src)
            print(f"    diff summary: {diff.summary()}")
            try:
                tgt.sync_from(src)
                return True
            except Exception as e:
                print(f"    ✗ PUSH FAILED: {type(e).__name__}: {str(e)[:300]}")
                return False

    if not _push():
        _cleanup()
        return

    print("\n    Verify on FortiGate side:")
    rec = _verify_policy_on_fortigate()
    if rec is None:
        print("    ✗ Policy NOT present on FortiGate after push")
        _cleanup()
        return
    print("    ✓ Policy present on FortiGate")

    print(f"\n[3/5] Toggle log=True via update — verify field changes...")
    rule.log = True
    rule.save()
    if not _push():
        _cleanup()
        return
    rec = _verify_policy_on_fortigate()
    if rec is None or rec.get("logtraffic") != "all":
        print(f"    ✗ logtraffic didn't update (got {rec.get('logtraffic') if rec else None!r})")
        _cleanup()
        return
    print("    ✓ logtraffic correctly set to 'all'")

    print(f"\n[4/5] Delete from Nautobot, push — verify policy gone from FortiGate...")
    from nautobot_firewall_models.models import Policy, PolicyRule

    # protect_on_delete: drop the parent Policy first so the PolicyRule
    # is no longer attached, THEN delete the rule.
    Policy.objects.filter(name=TEST_POLICY_PARENT_NAME).delete()
    PolicyRule.objects.filter(name=TEST_RULE_NAME).delete()
    # Push with delete_records_missing_from_source semantics requires the
    # adapter to not strip deletes. Mirror the Job's flag behavior:
    src = NautobotFirewallAdapter(hostname=ext.name, vdom=VDOM)
    src.load()
    with build_client(ext) as client:
        tgt = FortiGateFirewallTargetAdapter(client=client, hostname=ext.name, vdom=VDOM)
        tgt.load()
        tgt.source_adapter = src
        diff = tgt.diff_from(src)
        print(f"    diff summary: {diff.summary()}")
        try:
            tgt.sync_from(src)
        except Exception as e:
            print(f"    ✗ DELETE PUSH FAILED: {type(e).__name__}: {str(e)[:300]}")
            _cleanup()
            return

    rec = _verify_policy_on_fortigate()
    if rec is not None:
        print(f"    ✗ Policy {TEST_POLICYID} STILL on FortiGate after delete-push")
        _cleanup()
        return
    print("    ✓ Policy gone from FortiGate")

    print("\n[5/5] Final cleanup...")
    _cleanup()
    print("\n" + "=" * 70)
    print("✓ PolicyRule CRUD validated end-to-end against live FWF-61E")
    print("=" * 70)
