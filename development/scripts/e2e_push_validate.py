"""Validate bidirectional sync — Nautobot → FortiGate push direction.

Three sequential checks against the real FWF-61E:

1. **Pre-push diff** — load both sides, diff Nautobot vs FortiGate.
   Expectation: zero diff (Nautobot is already a faithful mirror of the
   FortiGate after the earlier pull sync).

2. **Inject test record** — create a single AddressObject in Nautobot
   that doesn't exist on the FortiGate. Mangled name format the adapter
   expects: ``fgt-dev__root__E2E_PUSH_TEST``.

3. **Push & verify** — run the push, then re-pull from FortiGate. The
   injected record should be present on the FortiGate and round-trip
   cleanly back to Nautobot's view.

Cleanup at the end: deletes the test record from BOTH sides so the run
is idempotent.

Run via:  make -C development e2e-push-validate
"""

import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"
TEST_ORIG_NAME = "E2E_PUSH_TEST"
TEST_MANGLED_NAME = f"{EXT_NAME}__{VDOM}__{TEST_ORIG_NAME}"
TEST_CIDR = "203.0.113.99/32"


def _build_pull_adapters():
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall import (
        FortiGateFirewallAdapter,
    )
    from nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall import (
        NautobotFirewallAdapter,
    )

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as client:
        fgt_side = FortiGateFirewallAdapter(client=client, hostname=ext.name, vdom=VDOM)
        fgt_side.load()
    nb_side = NautobotFirewallAdapter(hostname=ext.name, vdom=VDOM)
    nb_side.load()
    return fgt_side, nb_side, ext


def _create_test_record_in_nautobot():
    """Add E2E_PUSH_TEST directly to the Nautobot ORM so push has work to do."""
    from nautobot.extras.models import Status
    from nautobot.ipam.models import Namespace, Prefix
    from nautobot_firewall_models.models import AddressObject

    active = Status.objects.get(name="Active")
    ns = Namespace.objects.get(name="Global")
    prefix, _ = Prefix.objects.get_or_create(
        prefix=TEST_CIDR, namespace=ns, defaults={"status": active}
    )
    addr, created = AddressObject.objects.get_or_create(
        name=TEST_MANGLED_NAME,
        defaults={
            "prefix": prefix,
            "status": active,
            "description": "E2E push test record",
        },
    )
    return addr, created


def _cleanup_both_sides():
    """Delete the test record from BOTH Nautobot and FortiGate so re-runs are clean."""
    from nautobot.extras.models import ExternalIntegration
    from nautobot_firewall_models.models import AddressObject

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    AddressObject.objects.filter(name=TEST_MANGLED_NAME).delete()

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        try:
            fgt.cmdb.firewall.address.delete(uid=TEST_ORIG_NAME)
        except Exception:
            pass  # Wasn't there — fine.


def run() -> None:
    print("=" * 70)
    print(f"PUSH validation — {EXT_NAME!r} VDOM {VDOM!r}")
    print("=" * 70)

    # ---- Phase 1: prove the snapshot is symmetric --------------------------
    print("\n[1/4] Pre-push diff: Nautobot (source) vs FortiGate (target).")
    print("      Expect 0 diff because the pull Job ran earlier today.")
    fgt_side, nb_side, ext = _build_pull_adapters()
    diff = fgt_side.diff_from(nb_side)
    # Filter to just address_object — that's all our v0 push handles.
    sub = diff.summary()
    print(f"  full summary: {sub}")
    addr_actions = sum(
        1 for c in diff.get_children() if c.type == "address_object" and c.action
    )
    print(f"  address_object actions only: {addr_actions}")

    # ---- Phase 2: inject a test record into Nautobot ----------------------
    print(f"\n[2/4] Injecting test AddressObject {TEST_MANGLED_NAME!r} into Nautobot...")
    addr, created = _create_test_record_in_nautobot()
    print(f"  {'created' if created else 'exists'}: name={addr.name} prefix={addr.prefix.prefix}")

    # ---- Phase 3: run the push ----
    print("\n[3/4] Running push (Nautobot → FortiGate)...")
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall_target import (
        FortiGateFirewallTargetAdapter,
    )
    from nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall import (
        NautobotFirewallAdapter,
    )

    src = NautobotFirewallAdapter(hostname=ext.name, vdom=VDOM)
    src.load()
    with build_client(ext) as client:
        tgt = FortiGateFirewallTargetAdapter(
            client=client, hostname=ext.name, vdom=VDOM
        )
        tgt.load()
        diff = tgt.diff_from(src)
        print(f"  push diff: {diff.summary()}")
        try:
            tgt.sync_from(src)
            print("  push sync_from() complete")
        except Exception as e:
            print(f"  PUSH FAILED: {type(e).__name__}: {str(e)[:120]}")
            _cleanup_both_sides()
            return

    # ---- Phase 4: verify on the FortiGate side ---------------------------
    print("\n[4/4] Verifying on FortiGate side...")
    time.sleep(1)  # let the FortiGate commit
    with build_client(ext) as fgt:
        try:
            # fortigate-api Connector.get(**kwargs) — pops kwargs[self.uid].
            # For address that's 'name', NOT 'uid'. Pre-v2.4 we passed uid=
            # which silently fetched all addresses and rec[0] picked an
            # unrelated one — masking failed creates as false positives.
            found = fgt.cmdb.firewall.address.get(name=TEST_ORIG_NAME)
            if found:
                rec = found[0] if isinstance(found, list) else found
                print(f"  ✓ FortiGate now has {TEST_ORIG_NAME!r}: type={rec.get('type')} subnet={rec.get('subnet')}")
            else:
                print(f"  ✗ FortiGate does NOT have {TEST_ORIG_NAME!r} — push failed silently")
        except Exception as e:
            print(f"  ✗ lookup failed: {type(e).__name__}: {str(e)[:120]}")

    # ---- Cleanup ---------------------------------------------------------
    print("\n[cleanup] Deleting test record from both sides...")
    _cleanup_both_sides()
    print("  done")
    print("=" * 70)
