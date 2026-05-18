"""Inject a VLAN sub-interface + static route on fgt-dev to exercise v3.1 sync.

Idempotent — re-running re-applies the same config. Uses RFC 5737 docs IPs
for the route (``203.0.113.0/24``) and RFC 5737 for the VLAN sub-interface
IP (``198.51.100.1/24``) so any committed example output is sanitization-safe.

The VLAN parent is ``internal3`` (an unused operator port on the FortiWiFi-61E
test bed). The route's gateway is the FortiGate's own ``internal`` IP — even
though WAN isn't hooked up, FortiOS accepts the config and the route shows
up in ``cmdb/router/static``.

Run via:
    docker compose exec nautobot-web nautobot-server shell_plus --quiet-load \\
      --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_v31_inject_testdata.py').read()); run()"
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

VLAN_NAME = "ssot_test_vlan100"
# FortiWiFi-61E: internal1-7 are hard-switch members (can't VLAN-tag directly).
# wan1 is an unused physical port — safest VLAN parent on this test bed.
VLAN_PARENT = "wan1"
VLAN_ID = 100
VLAN_IP = "198.51.100.1 255.255.255.0"

ROUTE_SEQ = 9001
ROUTE_DST = "203.0.113.0 255.255.255.0"
# Route via wan2 (active DHCP interface) — FortiOS rejects routes whose egress
# is an interface it hasn't seen ARP/neighbor traffic on. wan2 has a live
# 192.168.1.x DHCP lease so its gateway resolves.
ROUTE_GATEWAY = "192.168.1.1"
ROUTE_DEVICE = "wan2"


def run():
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client
    from nautobot_ssot_fortinet.utils.fortios import check_fortios_response

    ei = ExternalIntegration.objects.get(name="fgt-dev")
    with build_client(ei) as c:
        # ── 1. Create the VLAN sub-interface ───────────────────────────────
        print(f"Creating VLAN interface {VLAN_NAME!r} on {VLAN_PARENT}, vlanid={VLAN_ID}...")
        existing_ifs = {i.get("name") for i in c.cmdb.system.interface.get()}
        if VLAN_NAME in existing_ifs:
            print(f"  exists — updating IP")
            check_fortios_response(
                c.cmdb.system.interface.update(
                    {"name": VLAN_NAME, "ip": VLAN_IP, "allowaccess": "ping"}
                ),
                label="vlan-update",
            )
        else:
            check_fortios_response(
                c.cmdb.system.interface.create(
                    {
                        "name": VLAN_NAME,
                        "type": "vlan",
                        "interface": VLAN_PARENT,
                        "vlanid": VLAN_ID,
                        "ip": VLAN_IP,
                        "allowaccess": "ping",
                        "vdom": "root",
                        "description": "v3.1 sync test — leave in place",
                    }
                ),
                label="vlan-create",
            )
            print(f"  created")

        # ── 2. Create the static route ─────────────────────────────────────
        print(f"\nCreating static route seq={ROUTE_SEQ}: {ROUTE_DST} via {ROUTE_GATEWAY} ({ROUTE_DEVICE})...")
        existing_routes = {r.get("seq-num") for r in c.cmdb.router.static.get()}
        if ROUTE_SEQ in existing_routes:
            print(f"  exists — updating")
            check_fortios_response(
                c.cmdb.router.static.update(
                    {
                        "seq-num": ROUTE_SEQ,
                        "dst": ROUTE_DST,
                        "gateway": ROUTE_GATEWAY,
                        "device": ROUTE_DEVICE,
                        "distance": 10,
                        "comment": "v3.1 sync test — leave in place",
                    }
                ),
                label="route-update",
            )
        else:
            check_fortios_response(
                c.cmdb.router.static.create(
                    {
                        "seq-num": ROUTE_SEQ,
                        "dst": ROUTE_DST,
                        "gateway": ROUTE_GATEWAY,
                        "device": ROUTE_DEVICE,
                        "distance": 10,
                        "comment": "v3.1 sync test — leave in place",
                    }
                ),
                label="route-create",
            )
            print(f"  created")

        # ── 3. Verify both round-trip from a fresh GET ────────────────────
        print("\nVerifying both records via fresh GETs...")
        all_ifs = c.cmdb.system.interface.get()
        the_vlan = next((i for i in all_ifs if i.get("name") == VLAN_NAME), None)
        print(f"  vlan {VLAN_NAME}: vlanid={the_vlan.get('vlanid')} interface={the_vlan.get('interface')} ip={the_vlan.get('ip')}")

        all_routes = c.cmdb.router.static.get()
        the_route = next((r for r in all_routes if r.get("seq-num") == ROUTE_SEQ), None)
        print(f"  route {ROUTE_SEQ}: dst={the_route.get('dst')} gw={the_route.get('gateway')} dev={the_route.get('device')}")

    print("\nInjection complete — left in place for future iteration.")
