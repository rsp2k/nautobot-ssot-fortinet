"""Validate ServiceObject push (full CRUD) against live FWF-61E.

ServiceObject is the exception in firewall-models: composite NK
(ip_protocol, port, name) so names are NOT mangled. Test uses a TCP
service on a high port unlikely to collide with anything real.

Phases:
  0. Cleanup any prior run
  1. Inject ServiceObject (TCP/65000)
  2. Push CREATE → verify on FortiGate
  3. Edit description, push UPDATE → verify
  4. Delete from Nautobot, push DELETE → verify gone
  5. Final cleanup

Run via:  make -C development e2e-push-service
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"
TEST_NAME = "e2e-svc-test"
TEST_PROTO = "TCP"
TEST_PORT = "65000"


def _cleanup() -> None:
    from nautobot.extras.models import ExternalIntegration
    from nautobot_firewall_models.models import ServiceObject

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    n = ServiceObject.objects.filter(name=TEST_NAME).delete()
    print(f"  Nautobot: ServiceObject={n}")

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        try:
            fgt.cmdb.firewall_service.custom.delete(uid=TEST_NAME)
            print(f"  FortiGate: deleted service {TEST_NAME!r}")
        except Exception:
            print(f"  FortiGate: service {TEST_NAME!r} wasn't there")


def _inject(description: str = "e2e service test"):
    from nautobot.extras.models import Status
    from nautobot_firewall_models.models import ServiceObject

    active = Status.objects.get(name="Active")
    svc, created = ServiceObject.objects.get_or_create(
        name=TEST_NAME,
        ip_protocol=TEST_PROTO,
        port=TEST_PORT,
        defaults={"status": active, "description": description},
    )
    return svc, created


def _verify_on_fortigate() -> dict | None:
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        found = fgt.cmdb.firewall_service.custom.get(name=TEST_NAME)
        if not found:
            return None
        rec = found[0] if isinstance(found, list) else found
        if isinstance(found, list):
            for r in found:
                if r.get("name") == TEST_NAME:
                    rec = r
                    break
            else:
                return None
        print(f"    name:     {rec.get('name')!r}")
        print(f"    protocol: {rec.get('protocol')!r}")
        print(f"    tcp-portrange: {rec.get('tcp-portrange')!r}")
        print(f"    comment:  {rec.get('comment')!r}")
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
    print(f"E2E ServiceObject push validation — ext={EXT_NAME!r} VDOM={VDOM!r}")
    print(f"  Test service: {TEST_NAME!r} = {TEST_PROTO}/{TEST_PORT}")
    print("=" * 70)

    print("\n[0/5] Cleanup prior runs...")
    _cleanup()

    print(f"\n[1/5] Inject ServiceObject {TEST_NAME!r}...")
    try:
        _inject()
    except Exception as e:
        print(f"  ✗ INJECT FAILED: {type(e).__name__}: {e}")
        _cleanup()
        return

    print(f"\n[2/5] Push CREATE — expect {TEST_NAME!r} on FortiGate...")
    if not _push():
        _cleanup()
        return
    print("    Verify on FortiGate:")
    rec = _verify_on_fortigate()
    if rec is None:
        print("    ✗ Not present")
        _cleanup()
        return
    if rec.get("tcp-portrange") != TEST_PORT:
        print(f"    ✗ tcp-portrange mismatch: expected {TEST_PORT!r}, got {rec.get('tcp-portrange')!r}")
        _cleanup()
        return
    print(f"    ✓ Present with tcp-portrange={TEST_PORT!r}")

    print(f"\n[3/5] Edit description, push UPDATE...")
    from nautobot_firewall_models.models import ServiceObject

    new_desc = "e2e UPDATED description"
    svc = ServiceObject.objects.get(name=TEST_NAME, ip_protocol=TEST_PROTO, port=TEST_PORT)
    svc.description = new_desc
    svc.save()
    if not _push():
        _cleanup()
        return
    rec = _verify_on_fortigate()
    if rec is None or new_desc not in (rec.get("comment") or ""):
        print(f"    ✗ comment didn't update: expected {new_desc!r}, got {rec.get('comment') if rec else None!r}")
        _cleanup()
        return
    print("    ✓ comment correctly updated")

    print(f"\n[4/5] Delete from Nautobot, push DELETE...")
    ServiceObject.objects.filter(name=TEST_NAME).delete()
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
    print("✓ ServiceObject CRUD validated end-to-end against live FWF-61E")
    print("=" * 70)
