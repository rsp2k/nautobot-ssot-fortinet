"""Live wireless sync against the configured ExternalIntegration `fgt-dev`.

Same pattern as ``e2e_live_firewall.py`` but for the wireless Job.

Run via:
    make -C development e2e-live-wireless
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"


def _build_adapters():
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_wireless import (
        FortiGateWirelessAdapter,
    )
    from nautobot_ssot_fortinet.diffsync.adapters.nautobot_wireless import (
        NautobotWirelessAdapter,
    )

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as client:
        src = FortiGateWirelessAdapter(
            client=client, hostname=ext.name, vdom=VDOM, sync_access_points=False
        )
        src.load()
    tgt = NautobotWirelessAdapter(
        hostname=ext.name, vdom=VDOM, sync_access_points=False
    )
    tgt.load()
    return src, tgt


def _summarize(src, tgt) -> None:
    for kind in ("wireless_network", "radio_profile", "access_point"):
        s = len(src.get_all(kind))
        t = len(tgt.get_all(kind))
        print(f"  {kind:22} source={s:3d}  target={t:3d}")


def _wipe(ext_name: str) -> None:
    from nautobot.wireless.models import RadioProfile, WirelessNetwork

    prefix = f"{ext_name}__{VDOM}__"
    n1 = WirelessNetwork.objects.filter(name__startswith=prefix).delete()
    print(f"  cleaned WirelessNetworks: {n1}")
    n2 = RadioProfile.objects.filter(name__startswith=prefix).delete()
    print(f"  cleaned RadioProfiles: {n2}")


def run() -> None:
    print("=" * 70)
    print(f"LIVE wireless sync — ext={EXT_NAME!r}, vdom={VDOM!r}")
    print("=" * 70)

    print("\n[0/4] Wipe prior runs for deterministic start:")
    _wipe(EXT_NAME)

    print("\n[1/4] Adapter loads (FortiGate REST + Nautobot ORM):")
    src, tgt = _build_adapters()
    _summarize(src, tgt)

    print("\n[2/4] First sync:")
    diff = tgt.diff_from(src)
    print(f"  diff summary: {diff.summary()}")
    try:
        tgt.sync_from(src)
        print("  sync_from() complete")
    except Exception as e:
        try:
            msgs = [f"{f}: {errs}" for f, errs in e.message_dict.items()]
        except Exception:
            msgs = [type(e).__name__]
        print(f"  SYNC FAILED: {msgs}")
        return

    print("\n[3/4] Idempotency check:")
    src2, tgt2 = _build_adapters()
    diff2 = tgt2.diff_from(src2)
    summary = diff2.summary()
    print(f"  second diff: {summary}")
    has_changes = any(v for k, v in summary.items() if k != "no-change" and v)
    if has_changes:
        print("  FAIL: second sync produced non-empty diff — NOT idempotent")
    else:
        print("  PASS: zero diffs on re-sync — fully idempotent against the live FortiGate")
    print("=" * 70)
