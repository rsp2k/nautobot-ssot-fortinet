"""Validate AddressObject push (full CRUD) against live FWF-61E.

Extends what e2e_push_validate.py covers (CREATE only) with UPDATE and
DELETE so all three operations have living e2e proof.

Risk model: throwaway name + RFC 5737 documentation IP; unconditional
cleanup.

Phases:
  0. Cleanup any prior run
  1. Inject Nautobot AddressObject (203.0.113.99/32)
  2. Push CREATE → verify on FortiGate with .get(name=)
  3. Edit prefix → 198.51.100.99/32, push UPDATE → verify
  4. Delete from Nautobot, push DELETE → verify gone
  5. Final cleanup

Run via:  make -C development e2e-push-address
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"
TEST_ORIG = "e2e-addr-test"
TEST_MANGLED = f"{EXT_NAME}__{VDOM}__{TEST_ORIG}"
TEST_CIDR_V1 = "203.0.113.99/32"
TEST_CIDR_V2 = "198.51.100.99/32"


def _cleanup() -> None:
    from nautobot.extras.models import ExternalIntegration
    from nautobot.ipam.models import Prefix
    from nautobot_firewall_models.models import AddressObject

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    n_a = AddressObject.objects.filter(name=TEST_MANGLED).delete()
    # Nautobot 3.x Prefix has network + prefix_length, not a `prefix` field
    # for filter queries — split the CIDR for filtering.
    n_p = (0, {})
    for cidr in (TEST_CIDR_V1, TEST_CIDR_V2):
        net, plen = cidr.split("/")
        deleted = Prefix.objects.filter(network=net, prefix_length=int(plen)).delete()
        n_p = (n_p[0] + deleted[0], {**n_p[1], **deleted[1]})
    print(f"  Nautobot: AddressObject={n_a} Prefix={n_p}")

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        try:
            fgt.cmdb.firewall.address.delete(uid=TEST_ORIG)
            print(f"  FortiGate: deleted address {TEST_ORIG!r}")
        except Exception:
            print(f"  FortiGate: address {TEST_ORIG!r} wasn't there")


def _inject(cidr: str):
    from nautobot.extras.models import Status
    from nautobot.ipam.models import Namespace, Prefix
    from nautobot_firewall_models.models import AddressObject

    active = Status.objects.get(name="Active")
    ns = Namespace.objects.get(name="Global")
    pfx, _ = Prefix.objects.get_or_create(
        prefix=cidr, namespace=ns, defaults={"status": active}
    )
    addr, created = AddressObject.objects.get_or_create(
        name=TEST_MANGLED,
        defaults={"prefix": pfx, "status": active, "description": "e2e address test"},
    )
    return addr, created


def _verify_on_fortigate() -> dict | None:
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        found = fgt.cmdb.firewall.address.get(name=TEST_ORIG)
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
        print(f"    type:   {rec.get('type')!r}")
        print(f"    subnet: {rec.get('subnet')!r}")
        print(f"    comment: {rec.get('comment')!r}")
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
    print(f"E2E AddressObject push validation — ext={EXT_NAME!r} VDOM={VDOM!r}")
    print(f"  Test name: {TEST_ORIG!r}  ({TEST_CIDR_V1} → {TEST_CIDR_V2})")
    print("=" * 70)

    print("\n[0/5] Cleanup prior runs...")
    _cleanup()

    print(f"\n[1/5] Inject AddressObject {TEST_MANGLED!r} = {TEST_CIDR_V1}...")
    try:
        _inject(TEST_CIDR_V1)
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
        print("    ✗ Not present after push")
        _cleanup()
        return
    print("    ✓ Present on FortiGate")

    print(f"\n[3/5] Update prefix → {TEST_CIDR_V2}, push UPDATE...")
    from nautobot.extras.models import Status
    from nautobot.ipam.models import Namespace, Prefix
    from nautobot_firewall_models.models import AddressObject

    active = Status.objects.get(name="Active")
    ns = Namespace.objects.get(name="Global")
    pfx2, _ = Prefix.objects.get_or_create(prefix=TEST_CIDR_V2, namespace=ns, defaults={"status": active})
    addr = AddressObject.objects.get(name=TEST_MANGLED)
    addr.prefix = pfx2
    addr.save()
    if not _push():
        _cleanup()
        return
    rec = _verify_on_fortigate()
    expected_subnet = TEST_CIDR_V2.split("/")[0]
    if rec is None or expected_subnet not in (rec.get("subnet") or ""):
        print(f"    ✗ subnet didn't update: expected {expected_subnet!r} in {rec.get('subnet') if rec else None!r}")
        _cleanup()
        return
    print(f"    ✓ subnet correctly contains {expected_subnet!r}")

    print(f"\n[4/5] Delete from Nautobot, push DELETE...")
    AddressObject.objects.filter(name=TEST_MANGLED).delete()
    if not _push():
        _cleanup()
        return
    rec = _verify_on_fortigate()
    if rec is not None:
        print(f"    ✗ Still present after delete-push: {rec.get('name')!r}")
        _cleanup()
        return
    print("    ✓ Gone from FortiGate")

    print("\n[5/5] Final cleanup...")
    _cleanup()
    print("\n" + "=" * 70)
    print("✓ AddressObject CRUD validated end-to-end against live FWF-61E")
    print("=" * 70)
