"""End-to-end wireless sync against the dev DB, fed by fixture data.

Mirrors ``e2e_firewall_sync.py`` for the wireless side: builds a mocked
fortigate-api client that returns ``wireless_{vap,wtp,wtp_profile}.json``
fixtures, then runs the full sync against the real ORM and checks
idempotency.

Run via:
    docker compose exec nautobot-web nautobot-server shell_plus --quiet-load \\
      --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_wireless_sync.py').read()); run()"

Or:  make -C development e2e-wireless
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

HOSTNAME = "fgt-dev"
VDOM = "root"
FIXTURES = Path("/opt/plugin/tests/fixtures")


def _make_client() -> MagicMock:
    client = MagicMock()
    client.cmdb.wireless_controller.vap.get.return_value = json.loads(
        (FIXTURES / "wireless_vap.json").read_text()
    )
    client.cmdb.wireless_controller.wtp_profile.get.return_value = json.loads(
        (FIXTURES / "wireless_wtp_profile.json").read_text()
    )
    client.cmdb.wireless_controller.wtp.get.return_value = json.loads(
        (FIXTURES / "wireless_wtp.json").read_text()
    )
    return client


def _build_adapters():
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_wireless import (
        FortiGateWirelessAdapter,
    )
    from nautobot_ssot_fortinet.diffsync.adapters.nautobot_wireless import (
        NautobotWirelessAdapter,
    )

    # sync_access_points=False by default — this matches the FWF-61E
    # scenario (no managed FortiAPs). When you want to test AP Device
    # sync, set the env vars E2E_AP_DEVICE_TYPE, E2E_AP_ROLE, E2E_AP_LOCATION
    # to existing Nautobot record names.
    import os

    ap_type = os.environ.get("E2E_AP_DEVICE_TYPE", "")
    ap_role = os.environ.get("E2E_AP_ROLE", "")
    ap_loc = os.environ.get("E2E_AP_LOCATION", "")
    sync_aps = bool(ap_type and ap_role and ap_loc)

    src = FortiGateWirelessAdapter(
        client=_make_client(),
        hostname=HOSTNAME,
        vdom=VDOM,
        sync_access_points=sync_aps,
        ap_device_type_model=ap_type,
        ap_role_name=ap_role,
        ap_location_name=ap_loc,
    )
    src.load()
    tgt = NautobotWirelessAdapter(
        hostname=HOSTNAME,
        vdom=VDOM,
        sync_access_points=sync_aps,
        ap_device_type_model=ap_type,
        ap_role_name=ap_role,
        ap_location_name=ap_loc,
    )
    tgt.load()
    return src, tgt, sync_aps


def _summarize(src, tgt, sync_aps: bool) -> None:
    kinds = ["wireless_network", "radio_profile"]
    if sync_aps:
        kinds.append("access_point")
    for kind in kinds:
        s = len(src.get_all(kind))
        t = len(tgt.get_all(kind))
        print(f"  {kind:22} source={s:3d}  target={t:3d}")


def _orm_counts() -> dict[str, int]:
    from nautobot.wireless.models import RadioProfile, WirelessNetwork

    prefix = f"{HOSTNAME}__{VDOM}__"
    return {
        "WirelessNetwork": WirelessNetwork.objects.filter(name__startswith=prefix).count(),
        "RadioProfile": RadioProfile.objects.filter(name__startswith=prefix).count(),
    }


def _wipe_scoped() -> None:
    """Wipe prefix-matched WirelessNetwork + RadioProfile records."""
    from nautobot.wireless.models import RadioProfile, WirelessNetwork

    prefix = f"{HOSTNAME}__{VDOM}__"
    n1 = WirelessNetwork.objects.filter(name__startswith=prefix).delete()
    print(f"  cleaned WirelessNetworks: {n1}")
    n2 = RadioProfile.objects.filter(name__startswith=prefix).delete()
    print(f"  cleaned RadioProfiles: {n2}")


def run() -> None:
    print("=" * 70)
    print(f"E2E wireless sync — hostname={HOSTNAME!r}, vdom={VDOM!r}")
    print("=" * 70)

    print("\n[0/4] Cleaning prefix-scoped wireless records:")
    _wipe_scoped()

    print("\n[1/4] Initial adapter loads:")
    src, tgt, sync_aps = _build_adapters()
    _summarize(src, tgt, sync_aps)
    if not sync_aps:
        print("  (AP Device sync skipped — set E2E_AP_DEVICE_TYPE+ROLE+LOCATION to enable)")

    print("\n[2/4] First sync — should be all creates:")
    diff = tgt.diff_from(src)
    print(f"  diff summary: {diff.summary()}")
    tgt.sync_from(src)
    print("  sync_from() complete")

    print("\n[3/4] ORM record counts after first sync:")
    for label, n in _orm_counts().items():
        print(f"  {label:22} = {n}")

    print("\n[4/4] Idempotency check — re-load + diff, expect no changes:")
    src2, tgt2, _ = _build_adapters()
    diff2 = tgt2.diff_from(src2)
    summary = diff2.summary()
    print(f"  second diff: {summary}")
    has_changes = any(v for k, v in summary.items() if k != "no-change" and v)
    if has_changes:
        print("  FAIL: second sync produced a non-empty diff — sync is NOT idempotent")
    else:
        print("  PASS: zero diffs on re-sync — fully idempotent")
    print("=" * 70)
