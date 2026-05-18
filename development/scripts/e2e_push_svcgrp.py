"""Validate ServiceObjectGroup push (full CRUD) against live FWF-61E.

Uses existing FortiGate services (HTTPS, DNS) as members so we don't
have to push services as a prerequisite. UPDATE phase swaps members
to validate the M2M change propagates.

Phases:
  0. Cleanup any prior run
  1. Inject ServiceObjectGroup with members=[HTTPS]
  2. Push CREATE → verify on FortiGate
  3. Add DNS to members, push UPDATE → verify
  4. Delete from Nautobot, push DELETE → verify gone
  5. Final cleanup

Run via:  make -C development e2e-push-svcgrp
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"
TEST_ORIG = "e2e-svcgrp-test"
TEST_MANGLED = f"{EXT_NAME}__{VDOM}__{TEST_ORIG}"


def _cleanup() -> None:
    from nautobot.extras.models import ExternalIntegration
    from nautobot_firewall_models.models import ServiceObjectGroup

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    n = ServiceObjectGroup.objects.filter(name=TEST_MANGLED).delete()
    print(f"  Nautobot: ServiceObjectGroup={n}")

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        try:
            fgt.cmdb.firewall_service.group.delete(uid=TEST_ORIG)
            print(f"  FortiGate: deleted service group {TEST_ORIG!r}")
        except Exception:
            print(f"  FortiGate: service group {TEST_ORIG!r} wasn't there")


def _inject(svc_names: list[str]):
    """svc_names are unmangled FortiOS service names (e.g. ['HTTPS'])."""
    from nautobot.extras.models import Status
    from nautobot_firewall_models.models import ServiceObject, ServiceObjectGroup

    active = Status.objects.get(name="Active")
    # ServiceObject has composite NK; .get by name is fine because names
    # are unique in practice on the dev device
    members = [ServiceObject.objects.get(name=n) for n in svc_names]
    grp, created = ServiceObjectGroup.objects.get_or_create(
        name=TEST_MANGLED,
        defaults={"status": active, "description": "e2e svcgrp test"},
    )
    grp.service_objects.set(members)
    grp.save()
    return grp, created


def _verify_on_fortigate() -> dict | None:
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        found = fgt.cmdb.firewall_service.group.get(name=TEST_ORIG)
        if not found:
            return None
        rec = found[0] if isinstance(found, list) else found
        if isinstance(found, list):
            for r in found:
                if r.get("name") == TEST_ORIG:
                    rec = r
                    break
            else:
                return None
        members = [m.get("name") for m in rec.get("member", [])]
        print(f"    name:    {rec.get('name')!r}")
        print(f"    members: {members}")
        return rec


def _push() -> bool:
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall_target import (
        FortiGateFirewallTargetAdapter,
    )
    from nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall import (
        NautobotFirewallAdapter,
    )

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
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


def run() -> None:
    print("=" * 70)
    print(f"E2E ServiceObjectGroup push validation — ext={EXT_NAME!r} VDOM={VDOM!r}")
    print(f"  Test group: {TEST_ORIG!r}")
    print("=" * 70)

    print("\n[0/5] Cleanup prior runs...")
    _cleanup()

    print(f"\n[1/5] Inject group with members=['HTTPS']...")
    try:
        _inject(["HTTPS"])
    except Exception as e:
        print(f"  ✗ INJECT FAILED: {type(e).__name__}: {e}")
        _cleanup()
        return

    print(f"\n[2/5] Push CREATE — expect {TEST_ORIG!r} on FortiGate...")
    if not _push():
        _cleanup()
        return
    print("    Verify on FortiGate:")
    rec = _verify_on_fortigate()
    if rec is None:
        print("    ✗ Not present")
        _cleanup()
        return
    members = [m.get("name") for m in rec.get("member", [])]
    if "HTTPS" not in members:
        print(f"    ✗ HTTPS not in members: {members}")
        _cleanup()
        return
    print("    ✓ Present with HTTPS member")

    print(f"\n[3/5] Add DNS to members, push UPDATE...")
    from nautobot_firewall_models.models import ServiceObject, ServiceObjectGroup

    grp = ServiceObjectGroup.objects.get(name=TEST_MANGLED)
    grp.service_objects.add(ServiceObject.objects.get(name="DNS"))
    grp.save()
    if not _push():
        _cleanup()
        return
    rec = _verify_on_fortigate()
    members = sorted([m.get("name") for m in rec.get("member", [])]) if rec else []
    if set(members) != {"HTTPS", "DNS"}:
        print(f"    ✗ members mismatch: expected {{HTTPS, DNS}}, got {set(members)}")
        _cleanup()
        return
    print("    ✓ Members correctly updated to ['DNS', 'HTTPS']")

    print(f"\n[4/5] Delete from Nautobot, push DELETE...")
    ServiceObjectGroup.objects.filter(name=TEST_MANGLED).delete()
    if not _push():
        _cleanup()
        return
    rec = _verify_on_fortigate()
    if rec is not None:
        print(f"    ✗ Still present after delete-push")
        _cleanup()
        return
    print("    ✓ Gone from FortiGate")

    print("\n[5/5] Final cleanup...")
    _cleanup()
    print("\n" + "=" * 70)
    print("✓ ServiceObjectGroup CRUD validated end-to-end against live FWF-61E")
    print("=" * 70)
