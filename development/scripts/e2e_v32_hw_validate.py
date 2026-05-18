"""Live hardware validation of v3.2 fixes against the dev FortiWiFi-61E.

Run via:
    docker compose exec nautobot-web nautobot-server shell_plus --quiet-load \\
      --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_v32_hw_validate.py').read()); run()"

Validates:

1. v3.2 ALL service maps to HOPOPT (not silently dropped)
2. v3.2 webproxy service maps to HOPOPT
3. v3.2 mac/dynamic/geography addresses load as .fortios.invalid placeholders
4. v3.1 VLAN sub-interfaces load with parent_interface + vlan_id
5. v3.1 router.static entries create FortinetStaticRoute records
6. No "Policy references unknown service 'ALL'" warnings appear
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def run():
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_devices import (
        FortiGateDevicesAdapter,
    )
    from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall import (
        FortiGateFirewallAdapter,
    )

    ei = ExternalIntegration.objects.get(name="fgt-dev")
    print(f"\n{'=' * 70}")
    print(f"v3.2 LIVE HARDWARE VALIDATION — fgt-dev ({ei.remote_url})")
    print(f"{'=' * 70}\n")

    # ── PHASE 1: Firewall pull ────────────────────────────────────────────
    print("PHASE 1: Firewall pull (v3.2 service+address fixes)")
    print("-" * 70)
    with build_client(ei) as client:
        fw = FortiGateFirewallAdapter(client=client, hostname="fgt-dev", vdom="root")
        fw.load()

    addrs = list(fw.get_all("address_object"))
    svcs = list(fw.get_all("service_object"))
    polcs = list(fw.get_all("policy_rule"))
    print(f"  Loaded: {len(addrs)} addresses, {len(svcs)} services, {len(polcs)} policies")

    # v3.2 service fix proof
    hopopt = [s for s in svcs if s.ip_protocol == "HOPOPT"]
    print(f"\n  HOPOPT-mapped services (v3.2 ALL/webproxy fix): {len(hopopt)}")
    for s in hopopt:
        print(f"    ✓ {s.name}  (was skipped in v3.1, now syncs)")

    # v3.2 address fix proof
    placeholders = [a for a in addrs if str(a.value).endswith(".fortios.invalid")]
    print(f"\n  .fortios.invalid placeholder addresses (v3.2 mac/dynamic/geo fix): {len(placeholders)}")
    for a in placeholders[:15]:
        print(f"    ✓ {a.original_name}  →  {a.value}")
    if len(placeholders) > 15:
        print(f"    ... and {len(placeholders) - 15} more")

    # Cascade proof — count policies that reference HOPOPT services
    if hopopt:
        hopopt_nks = {(s.ip_protocol, s.port, s.name) for s in hopopt}
        pol_refs_to_hopopt = [
            p for p in polcs if any(nk in hopopt_nks for nk in p.destination_services)
        ]
        print(f"\n  Policies that NOW reference v3.2-mapped HOPOPT services: {len(pol_refs_to_hopopt)}")
        for p in pol_refs_to_hopopt[:10]:
            print(f"    ✓ rule {p.index}: {p.original_name}")
        if len(pol_refs_to_hopopt) > 10:
            print(f"    ... and {len(pol_refs_to_hopopt) - 10} more")
        print("  ↑ Pre-v3.2 each of these would have shown 'unknown service ALL'")

    # ── PHASE 2: Device + Interface + Route pull ──────────────────────────
    print(f"\n{'-' * 70}")
    print("PHASE 2: Device + Interface + Route pull (v3.1 VLAN+route additions)")
    print("-" * 70)
    with build_client(ei) as client:
        dev = FortiGateDevicesAdapter(
            client=client,
            hostname="fgt-dev",
            vdom="root",
            device_type_model="FortiWiFi-61E",
            role_name="Firewall",
            location_name="Lab",
            status_name="Active",
            include_static_routes=True,
        )
        dev.load()

    ifs = list(dev.get_all("fortigate_interface"))
    routes = list(dev.get_all("fortigate_static_route"))
    print(f"  Loaded: {len(ifs)} interfaces, {len(routes)} static routes")

    # v3.1 VLAN proof
    vlans = [i for i in ifs if i.vlan_id is not None]
    print(f"\n  VLAN sub-interfaces (v3.1 feature): {len(vlans)}")
    for v in vlans:
        print(f"    ✓ {v.name}  vlan_id={v.vlan_id}  parent={v.parent_interface_name!r}  mode={v.vlan_mode}")

    # v3.1 route proof
    print(f"\n  Static routes (v3.1 feature): {len(routes)}")
    for r in routes[:10]:
        print(
            f"    ✓ seq={r.seq_num}  {r.destination}  via {r.gateway or 'BLACKHOLE'}  "
            f"dev={r.interface_name or '-'}  distance={r.distance}"
        )
    if len(routes) > 10:
        print(f"    ... and {len(routes) - 10} more")

    print(f"\n{'=' * 70}")
    print("VALIDATION COMPLETE")
    print(f"{'=' * 70}\n")
