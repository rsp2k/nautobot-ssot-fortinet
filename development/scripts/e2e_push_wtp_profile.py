"""Validate v2.2 wtp-profile CREATE via sibling aggregation against live FWF-61E.

The previous push validation (``e2e_push_validate.py``) covers AddressObject
push. This script covers the wireless side — specifically the new code path
introduced in v2026.05.18.3 where a brand-new wtp-profile is created on the
FortiGate from sibling RadioProfiles in Nautobot.

Risk model: uses a throwaway ``original_profile_name`` that doesn't collide
with anything on the device. Cleanup always runs (success or failure) so
re-runs are deterministic. None of the device's existing wtp-profiles
(``office``, ``default``) are touched.

Phases:
  1. Cleanup any prior run
  2. Inject TWO RadioProfile records in Nautobot for the same throwaway
     ``original_profile_name`` (radio-1 + radio-2)
  3. Run the wireless push end-to-end
  4. Verify the FortiGate now has the new wtp-profile with BOTH radios

Run via:  make -C development e2e-push-wtp
"""

import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"
TEST_PROFILE = "e2e-wtp-test"
# Mangled form is <host>__<vdom>__<profile>__radioN — no dash between
# "radio" and the digit. Matches mangle_name() output in the pull adapter.
TEST_RP1_NAME = f"{EXT_NAME}__{VDOM}__{TEST_PROFILE}__radio1"
TEST_RP2_NAME = f"{EXT_NAME}__{VDOM}__{TEST_PROFILE}__radio2"


def _cleanup_both_sides() -> None:
    """Wipe test wtp-profile from FortiGate AND test RadioProfiles from Nautobot."""
    from nautobot.extras.models import ExternalIntegration
    from nautobot.wireless.models import RadioProfile as ORMRadioProfile

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    # Nautobot side
    n = ORMRadioProfile.objects.filter(
        name__in=[TEST_RP1_NAME, TEST_RP2_NAME]
    ).delete()
    print(f"  cleaned Nautobot RadioProfiles: {n}")

    # FortiGate side
    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        try:
            fgt.cmdb.wireless_controller.wtp_profile.delete(uid=TEST_PROFILE)
            print(f"  cleaned FortiGate wtp-profile {TEST_PROFILE!r}")
        except Exception:
            print(f"  FortiGate wtp-profile {TEST_PROFILE!r} wasn't there — fine")


def _create_test_radio_profiles_in_nautobot() -> tuple[int, int]:
    """Create 2 RadioProfile records in Nautobot under the throwaway profile name."""
    from nautobot.wireless.models import RadioProfile as ORMRadioProfile

    rp1, c1 = ORMRadioProfile.objects.get_or_create(
        name=TEST_RP1_NAME,
        defaults={
            "frequency": "2.4GHz",
            "tx_power_min": 3,
            "tx_power_max": 15,
            "regulatory_domain": "US",
            "allowed_channel_list": [1, 6, 11],
        },
    )
    rp2, c2 = ORMRadioProfile.objects.get_or_create(
        name=TEST_RP2_NAME,
        defaults={
            "frequency": "5GHz",
            "tx_power_min": 5,
            "tx_power_max": 17,
            "regulatory_domain": "US",
            "allowed_channel_list": [36, 40, 44, 48],
        },
    )
    return (1 if c1 else 0), (1 if c2 else 0)


def run() -> None:
    print("=" * 70)
    print(f"E2E v2.2 wtp-profile CREATE validation — {EXT_NAME!r} VDOM {VDOM!r}")
    print(f"  Test wtp-profile name: {TEST_PROFILE!r}")
    print("=" * 70)

    print("\n[0/4] Cleanup prior runs...")
    _cleanup_both_sides()

    print("\n[1/4] Inject 2 RadioProfiles in Nautobot (radio-1 @ 2.4GHz + radio-2 @ 5GHz)")
    c1, c2 = _create_test_radio_profiles_in_nautobot()
    print(f"  radio-1 created={bool(c1)}  radio-2 created={bool(c2)}")

    print("\n[2/4] Run wireless push (Nautobot → FortiGate)...")
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
    src_rps = [rp for rp in src.get_all("radio_profile") if rp.original_profile_name == TEST_PROFILE]
    print(f"  source has {len(src_rps)} RadioProfiles for {TEST_PROFILE!r}")
    for rp in src_rps:
        print(f"    - {rp.name}  radio-{rp.radio_index} {rp.frequency}")

    with build_client(ext) as client:
        tgt = FortiGateWirelessTargetAdapter(client=client, hostname=ext.name, vdom=VDOM)
        tgt.load()
        # v2.2 plumbing — what the Job does for us in production
        tgt.source_adapter = src
        diff = tgt.diff_from(src)
        summary = diff.summary()
        print(f"  diff summary: {summary}")
        try:
            tgt.sync_from(src)
            print("  sync_from() complete")
        except Exception as e:
            print(f"  ✗ PUSH FAILED: {type(e).__name__}: {str(e)[:200]}")
            _cleanup_both_sides()
            return

    print("\n[3/4] Verify on FortiGate side...")
    time.sleep(1)
    with build_client(ext) as fgt:
        try:
            # fortigate-api Connector.get(**kwargs) — pops kwargs[self.uid].
            # For wtp_profile that's 'name', NOT 'uid'. Pre-v2.4 we passed
            # uid= which silently fetched all profiles and rec[0] picked an
            # unrelated record — masking failed creates as false positives.
            found = fgt.cmdb.wireless_controller.wtp_profile.get(name=TEST_PROFILE)
            if not found:
                print(f"  ✗ FortiGate does NOT have wtp-profile {TEST_PROFILE!r}")
                _cleanup_both_sides()
                return
            rec = found[0] if isinstance(found, list) else found
            print(f"  ✓ FortiGate has wtp-profile {TEST_PROFILE!r}")
            print(f"    platform-mode: {rec.get('platform-mode')!r}")
            print(f"    comment:       {rec.get('comment')!r}")
            r1 = rec.get("radio-1", {})
            r2 = rec.get("radio-2", {})
            r1_band = r1.get("band") if isinstance(r1, dict) else None
            r2_band = r2.get("band") if isinstance(r2, dict) else None
            print(f"    radio-1 band:  {r1_band!r}")
            print(f"    radio-2 band:  {r2_band!r}")
            if not r1_band or not r2_band:
                print("  ✗ One of the radios is empty — aggregation didn't include both")
            else:
                print("  ✓ Both radios populated — sibling aggregation worked end-to-end")
        except Exception as e:
            print(f"  ✗ lookup failed: {type(e).__name__}: {str(e)[:200]}")

    print("\n[4/4] Cleanup test artifacts from both sides...")
    _cleanup_both_sides()
    print("=" * 70)
