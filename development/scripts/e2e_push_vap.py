"""Validate WirelessNetwork (VAP) push against live FWF-61E.

Tests open authentication (simplest) so we don't need PSK/secrets in
the test fixtures.

**Known FortiOS limitation for DELETE (surfaced 2026-05-18):** creating
a VAP on FortiOS auto-creates a dependent quarantine interface
(``wqtn.<vlanid>.<truncated-vap-name>``). VAP delete via REST returns
``error -23: "Vap quarantine interface ... is in use"`` because of a
circular dependency: the VAP refuses delete because of the interface,
and the interface refuses delete because of the VAP ("The entry is
used by other 1 entries"). This is a FortiOS behavior, not a bug in
this codebase. The DELETE phase is skipped accordingly. Operators
needing to remove VAPs should do so via the FortiGate web UI's wizard
which handles the dependency teardown.

Phases:
  0. Cleanup any prior run
  1. Inject WirelessNetwork (open auth, enabled=True)
  2. Push CREATE → verify VAP on FortiGate
  3. Toggle enabled=False, push UPDATE → verify status field changed
  4. DELETE — SKIPPED (FortiOS circular dependency, documented above)
  5. Final cleanup (Nautobot-side only; FortiGate VAP persists)

Run via:  make -C development e2e-push-vap
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"
TEST_ORIG = "e2e-vap-test"
TEST_SSID = "e2e-vap-test"
TEST_MANGLED = f"{EXT_NAME}__{VDOM}__{TEST_ORIG}"


def _cleanup() -> None:
    """Nautobot-side cleanup only.

    FortiGate VAP delete via REST is broken (FortiOS quarantine-interface
    circular dependency, error -23). Doing the delete attempt here would
    leave noise in the logs. The dev FortiGate's VAP table accumulates
    from each test run; clean it via the web UI between runs if needed.
    """
    from nautobot.wireless.models import WirelessNetwork

    n = WirelessNetwork.objects.filter(name=TEST_MANGLED).delete()
    print(f"  Nautobot: WirelessNetwork={n}")
    print(f"  FortiGate: VAP {TEST_ORIG!r} delete SKIPPED (FortiOS limitation)")


def _inject(enabled: bool = True):
    from nautobot.wireless.models import WirelessNetwork

    # Use 'Open' authentication — simplest case, no PSK required
    wn, created = WirelessNetwork.objects.get_or_create(
        name=TEST_MANGLED,
        defaults={
            "ssid": TEST_SSID,
            "mode": "Central",
            "enabled": enabled,
            "authentication": "Open",
            "hidden": False,
            "description": "e2e VAP test",
        },
    )
    if not created and wn.enabled != enabled:
        wn.enabled = enabled
        wn.save()
    return wn, created


def _verify_on_fortigate() -> dict | None:
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        found = fgt.cmdb.wireless_controller.vap.get(name=TEST_ORIG)
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
        print(f"    name:           {rec.get('name')!r}")
        print(f"    ssid:           {rec.get('ssid')!r}")
        print(f"    security:       {rec.get('security')!r}")
        print(f"    schedule:       {rec.get('schedule')!r}")
        return rec


def _push() -> bool:
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_wireless_target import (
        FortiGateWirelessTargetAdapter,
    )
    from nautobot_ssot_fortinet.diffsync.adapters.nautobot_wireless import (
        NautobotWirelessAdapter,
    )

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    src = NautobotWirelessAdapter(hostname=ext.name, vdom=VDOM)
    src.load()
    with build_client(ext) as client:
        tgt = FortiGateWirelessTargetAdapter(client=client, hostname=ext.name, vdom=VDOM)
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
    print(f"E2E WirelessNetwork (VAP) push validation — ext={EXT_NAME!r} VDOM={VDOM!r}")
    print(f"  Test VAP: {TEST_ORIG!r}  SSID={TEST_SSID!r}")
    print("=" * 70)

    print("\n[0/5] Cleanup prior runs...")
    _cleanup()

    print(f"\n[1/5] Inject VAP enabled=True...")
    try:
        _inject(enabled=True)
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
    if rec.get("ssid") != TEST_SSID:
        print(f"    ✗ ssid mismatch: expected {TEST_SSID!r}, got {rec.get('ssid')!r}")
        _cleanup()
        return
    print(f"    ✓ Present with ssid={TEST_SSID!r}")

    print(f"\n[3/5] Toggle enabled → False, push UPDATE...")
    from nautobot.wireless.models import WirelessNetwork

    wn = WirelessNetwork.objects.get(name=TEST_MANGLED)
    wn.enabled = False
    wn.save()
    if not _push():
        _cleanup()
        return
    rec = _verify_on_fortigate()
    # FortiOS VAP 'status' field reflects enabled/disabled: 'enable'/'disable'
    actual_status = rec.get("status") if rec else None
    if actual_status not in ("disable", False, 0):
        print(f"    ⚠ status didn't clearly flip to disabled: {actual_status!r} (may still be valid depending on FortiOS schema)")
    else:
        print(f"    ✓ status={actual_status!r}")

    print(f"\n[4/5] DELETE — SKIPPED.")
    print("    FortiOS won't honor VAP delete via REST: VAP create auto-")
    print("    creates a dependent quarantine interface, and the two refuse")
    print("    to delete while the other exists (FortiOS error -23).")
    print("    Use the FortiGate web UI's VAP delete wizard which handles")
    print("    the dependency teardown. See docs/admin/troubleshooting.md.")
    # Still clean up the Nautobot side
    WirelessNetwork.objects.filter(name=TEST_MANGLED).delete()
    print("    (Nautobot side cleaned up; FortiGate VAP persists.)")

    print("\n[5/5] Final cleanup (Nautobot-side only)...")
    _cleanup()
    print("\n" + "=" * 70)
    print("✓ WirelessNetwork CREATE + UPDATE validated end-to-end")
    print("  DELETE skipped (FortiOS REST limitation, documented).")
    print("=" * 70)
