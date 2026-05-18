"""Inject a dstaddr-form route + named AddressObject on fgt-dev to exercise v3.2.6.

Creates:
1. An AddressObject named ``ssot_test_dstaddr`` with subnet ``192.0.2.0/24``
   (RFC 5737 documentation range)
2. A static route seq=9002 with ``dstaddr=[{"name": "ssot_test_dstaddr"}]``
   instead of ``dst=...`` — the form v3.2.5 and earlier silently skipped.

Idempotent — re-runs update both. RFC 5737 IPs throughout.

Run via:
    docker compose exec nautobot-web nautobot-server shell_plus --quiet-load \\
      --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_v326_inject_dstaddr.py').read()); run()"
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ADDR_NAME = "ssot_test_dstaddr"
ADDR_SUBNET = "192.0.2.0 255.255.255.0"

ROUTE_SEQ = 9002
ROUTE_GATEWAY = "192.168.1.1"  # uses the active DHCP-attached interface
ROUTE_DEVICE = "wan2"


def run():
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client
    from nautobot_ssot_fortinet.utils.fortios import check_fortios_response

    ei = ExternalIntegration.objects.get(name="fgt-dev")
    with build_client(ei) as c:
        # 1. AddressObject
        print(f"Creating/updating AddressObject {ADDR_NAME!r} = {ADDR_SUBNET}...")
        existing = {a.get("name") for a in c.cmdb.firewall.address.get()}
        if ADDR_NAME in existing:
            print("  exists — updating subnet")
            check_fortios_response(
                c.cmdb.firewall.address.update({"name": ADDR_NAME, "subnet": ADDR_SUBNET}),
                label="addr-update",
            )
        else:
            check_fortios_response(
                c.cmdb.firewall.address.create(
                    {"name": ADDR_NAME, "type": "ipmask", "subnet": ADDR_SUBNET,
                     "comment": "v3.2.6 dstaddr-resolver test — leave in place"}
                ),
                label="addr-create",
            )
            print("  created")

        # 2. Route using dstaddr (not dst)
        print(f"\nCreating/updating route seq={ROUTE_SEQ} with dstaddr=[{ADDR_NAME!r}]...")
        existing_routes = {r.get("seq-num") for r in c.cmdb.router.static.get()}
        # FortiOS dstaddr format: list of {"name": "<address-name>"}
        if ROUTE_SEQ in existing_routes:
            print("  exists — updating")
            check_fortios_response(
                c.cmdb.router.static.update(
                    {
                        "seq-num": ROUTE_SEQ,
                        "dstaddr": [{"name": ADDR_NAME}],
                        "gateway": ROUTE_GATEWAY,
                        "device": ROUTE_DEVICE,
                        "distance": 10,
                        "comment": "v3.2.6 dstaddr-resolver test — leave in place",
                    }
                ),
                label="route-update",
            )
        else:
            check_fortios_response(
                c.cmdb.router.static.create(
                    {
                        "seq-num": ROUTE_SEQ,
                        "dstaddr": [{"name": ADDR_NAME}],
                        "gateway": ROUTE_GATEWAY,
                        "device": ROUTE_DEVICE,
                        "distance": 10,
                        "comment": "v3.2.6 dstaddr-resolver test — leave in place",
                    }
                ),
                label="route-create",
            )
            print("  created")

        # 3. Verify via fresh GETs
        print("\nVerifying...")
        addr = next(a for a in c.cmdb.firewall.address.get() if a.get("name") == ADDR_NAME)
        print(f"  address: type={addr.get('type')} subnet={addr.get('subnet')}")
        route = next(r for r in c.cmdb.router.static.get() if r.get("seq-num") == ROUTE_SEQ)
        print(f"  route:   seq={route.get('seq-num')} dst={route.get('dst')!r} dstaddr={route.get('dstaddr')}")

    print("\nInjection complete — left in place per session convention.")
