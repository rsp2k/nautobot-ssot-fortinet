"""Validate NATPolicyRule push (VIP create/update/delete) against live FWF-61E.

The VIP create path has the trickiest dependency chain in the codebase:
the synthesized ``vip_<name>_ext`` and ``vip_<name>_mapped`` AddressObjects
must be created on the FortiGate AND show up in the target adapter's
store BEFORE ``FortiGateNATPolicyRule.create()`` runs its
``_lookup_synth_addr_value()`` lookup. Whether DiffSync's ordering
makes this work end-to-end has never been live-tested.

Risk model: ``e2e-nat-test`` VIP name + RFC 5737 documentation IPs
(203.0.113.99 → 10.0.0.50) so we don't collide with anything real on
the device or its routable networks. Unconditional cleanup.

Phases:
  0. Cleanup any prior run
  1. Inject prereqs in Nautobot:
     - Prefix 203.0.113.0/24 + IPAddress 203.0.113.99 (ext)
     - Prefix 10.0.0.0/24 + IPAddress 10.0.0.50 (mapped)
     - AddressObject vip_e2e-nat-test_ext (ip_address FK)
     - AddressObject vip_e2e-nat-test_mapped (ip_address FK)
     - NATPolicy parent + NATPolicyRule with [extintf=wan1]
  2. Push — verify VIP lands on FortiGate
  3. Update mappedip via translated_destination_addresses change
  4. Delete — verify VIP gone
  5. Final cleanup

Run via:  make -C development e2e-push-nat
"""

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"
TEST_VIP = "e2e-nat-test"
TEST_RULE_NAME = f"{EXT_NAME}__{VDOM}__nat_rule_{TEST_VIP}"
TEST_NAT_POLICY = f"{EXT_NAME}__{VDOM}__e2e-nat-policy"
TEST_EXT_ADDR = f"{EXT_NAME}__{VDOM}__vip_{TEST_VIP}_ext"
TEST_MAPPED_ADDR = f"{EXT_NAME}__{VDOM}__vip_{TEST_VIP}_mapped"
TEST_EXT_IP = "203.0.113.99"
TEST_EXT_PREFIX = "203.0.113.0/24"
TEST_MAPPED_IP = "10.0.0.50"
TEST_MAPPED_IP_V2 = "10.0.0.99"  # for update test
TEST_MAPPED_PREFIX = "10.0.0.0/24"


def _cleanup() -> None:
    from nautobot.extras.models import ExternalIntegration
    from nautobot.ipam.models import IPAddress, Prefix
    from nautobot_firewall_models.models import AddressObject, NATPolicy, NATPolicyRule

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    # Parent NATPolicy first (protect_on_delete)
    n_np = NATPolicy.objects.filter(name=TEST_NAT_POLICY).delete()
    n_npr = NATPolicyRule.objects.filter(name=TEST_RULE_NAME).delete()
    # v2.6: edit-value test reuses the same AddressObject — no _mapped_v2
    # to clean up. (The _v2 cleanup is kept harmless via the v2 IP filter
    # below in case any leftover test data exists from prior runs.)
    n_a = AddressObject.objects.filter(name__in=[TEST_EXT_ADDR, TEST_MAPPED_ADDR]).delete()
    # IPAddress objects (catch our test IPs, including the v2 update IP)
    n_ip = IPAddress.objects.filter(host__in=[TEST_EXT_IP, TEST_MAPPED_IP, TEST_MAPPED_IP_V2]).delete()
    # Prefixes are shared infra — leave them alone
    print(f"  Nautobot: NATPolicy={n_np} Rule={n_npr} Addr={n_a} IP={n_ip}")

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        try:
            fgt.cmdb.firewall.vip.delete(uid=TEST_VIP)
            print(f"  FortiGate: deleted VIP {TEST_VIP!r}")
        except Exception:
            print(f"  FortiGate: VIP {TEST_VIP!r} wasn't there")
        # The synth addresses also live on the FortiGate after push
        for fortios_addr in (
            f"vip_{TEST_VIP}_ext",
            f"vip_{TEST_VIP}_mapped",
            f"vip_{TEST_VIP}_mapped_v2",
        ):
            try:
                fgt.cmdb.firewall.address.delete(uid=fortios_addr)
            except Exception:
                pass


def _inject_test_records():
    from nautobot.extras.models import Status
    from nautobot.ipam.models import IPAddress, Namespace, Prefix
    from nautobot_firewall_models.models import (
        AddressObject,
        NATPolicy,
        NATPolicyRule,
    )

    active = Status.objects.get(name="Active")
    ns = Namespace.objects.get(name="Global")

    # IPAM prereqs. Nautobot 3.x requires IPAddress.parent to be an
    # exact-mask prefix (/32 for host IPs), NOT a larger containing
    # prefix. Create /32 prefixes per-host.
    ext_pfx, _ = Prefix.objects.get_or_create(
        prefix=f"{TEST_EXT_IP}/32", namespace=ns, defaults={"status": active}
    )
    mapped_pfx, _ = Prefix.objects.get_or_create(
        prefix=f"{TEST_MAPPED_IP}/32", namespace=ns, defaults={"status": active}
    )
    ext_ip, _ = IPAddress.objects.get_or_create(
        host=TEST_EXT_IP,
        defaults={"status": active, "mask_length": 32, "parent": ext_pfx},
    )
    mapped_ip, _ = IPAddress.objects.get_or_create(
        host=TEST_MAPPED_IP,
        defaults={"status": active, "mask_length": 32, "parent": mapped_pfx},
    )

    # Synth-style AddressObjects (ip_address FK → produces address_type='ipaddress')
    ext_addr, _ = AddressObject.objects.get_or_create(
        name=TEST_EXT_ADDR,
        defaults={"ip_address": ext_ip, "status": active, "description": f"VIP {TEST_VIP} external IP"},
    )
    mapped_addr, _ = AddressObject.objects.get_or_create(
        name=TEST_MAPPED_ADDR,
        defaults={"ip_address": mapped_ip, "status": active, "description": f"VIP {TEST_VIP} mapped IP"},
    )

    nat_policy, _ = NATPolicy.objects.get_or_create(
        name=TEST_NAT_POLICY, defaults={"status": active}
    )

    rule, created = NATPolicyRule.objects.get_or_create(
        name=TEST_RULE_NAME,
        defaults={
            "status": active,
            "remark": False,
            "log": False,
            # external_interface gets parsed from description's [extintf=X]
            "description": f"e2e NAT test [extintf=wan1]",
        },
    )
    rule.original_destination_addresses.set([ext_addr])
    rule.translated_destination_addresses.set([mapped_addr])
    rule.save()
    nat_policy.nat_policy_rules.add(rule)
    print(f"  NATPolicyRule {TEST_RULE_NAME!r}: created={created}")
    return rule, mapped_ip


def _verify_vip_on_fortigate() -> dict | None:
    from nautobot.extras.models import ExternalIntegration

    from nautobot_ssot_fortinet.clients.fortigate import build_client

    ext = ExternalIntegration.objects.get(name=EXT_NAME)
    with build_client(ext) as fgt:
        found = fgt.cmdb.firewall.vip.get(name=TEST_VIP)
        if not found:
            return None
        rec = found[0] if isinstance(found, list) else found
        if isinstance(found, list):
            for v in found:
                if v.get("name") == TEST_VIP:
                    rec = v
                    break
            else:
                return None
        mapped = [m.get("range") for m in rec.get("mappedip", [])]
        print(f"    name:    {rec.get('name')!r}")
        print(f"    extip:   {rec.get('extip')!r}")
        print(f"    extintf: {rec.get('extintf')!r}")
        print(f"    mappedip ranges: {mapped}")
        print(f"    comment: {rec.get('comment')!r}")
        print(f"    portforward: {rec.get('portforward')!r}")
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
    print(f"E2E NATPolicyRule push validation — ext={EXT_NAME!r} VDOM={VDOM!r}")
    print(f"  Test VIP: {TEST_VIP!r}  ({TEST_EXT_IP} → {TEST_MAPPED_IP})")
    print("=" * 70)

    print("\n[0/5] Cleanup prior runs...")
    _cleanup()

    print("\n[1/5] Inject Nautobot prereqs (prefixes, IPs, addrs, NAT policy + rule)...")
    try:
        rule, mapped_ip = _inject_test_records()
    except Exception as e:
        print(f"  ✗ INJECT FAILED: {type(e).__name__}: {e}")
        _cleanup()
        return

    print(f"\n[2/5] Run firewall push (create) — expect VIP {TEST_VIP!r} on FortiGate...")
    if not _push():
        _cleanup()
        return
    print("\n    Verify on FortiGate side:")
    rec = _verify_vip_on_fortigate()
    if rec is None:
        print(f"    ✗ VIP {TEST_VIP!r} NOT present on FortiGate after push")
        _cleanup()
        return
    if rec.get("extip") != TEST_EXT_IP:
        print(f"    ✗ extip mismatch: expected {TEST_EXT_IP!r}, got {rec.get('extip')!r}")
        _cleanup()
        return
    mapped_ranges = [m.get("range") for m in rec.get("mappedip", [])]
    if TEST_MAPPED_IP not in mapped_ranges:
        print(f"    ✗ mappedip mismatch: expected {TEST_MAPPED_IP!r} in {mapped_ranges}")
        _cleanup()
        return
    print("    ✓ VIP present with correct extip + mappedip")

    print(f"\n[3/5] Update mappedip {TEST_MAPPED_IP} → {TEST_MAPPED_IP_V2} via EDIT-VALUE on existing AddressObject...")
    # v2.6+: the rule's resolved_mappedip DiffSync attr fingerprints the
    # ACTUAL IP value (not just the M2M record name). Editing the IP on
    # the existing synth address now produces a rule-level diff →
    # NATPolicyRule.update() fires → vip.update() propagates to FortiOS.
    # Pre-v2.6 this scenario was a known dead-end (operators had to
    # replace the AddressObject reference).
    from nautobot.extras.models import Status
    from nautobot.ipam.models import IPAddress, Namespace, Prefix
    from nautobot_firewall_models.models import AddressObject

    active = Status.objects.get(name="Active")
    ns = Namespace.objects.get(name="Global")
    mapped_pfx_v2, _ = Prefix.objects.get_or_create(
        prefix=f"{TEST_MAPPED_IP_V2}/32", namespace=ns, defaults={"status": active}
    )
    mapped_ip2, _ = IPAddress.objects.get_or_create(
        host=TEST_MAPPED_IP_V2,
        defaults={"status": active, "mask_length": 32, "parent": mapped_pfx_v2},
    )
    # Edit the EXISTING mapped AddressObject (don't create a new one)
    mapped_addr = AddressObject.objects.get(name=TEST_MAPPED_ADDR)
    mapped_addr.ip_address = mapped_ip2
    mapped_addr.save()
    if not _push():
        _cleanup()
        return
    rec = _verify_vip_on_fortigate()
    mapped_ranges_after = [m.get("range") for m in rec.get("mappedip", [])] if rec else []
    if TEST_MAPPED_IP_V2 not in mapped_ranges_after:
        print(f"    ✗ updated mappedip not present: expected {TEST_MAPPED_IP_V2!r} in {mapped_ranges_after}")
        _cleanup()
        return
    print(f"    ✓ mappedip updated to {TEST_MAPPED_IP_V2!r}")

    print(f"\n[4/5] Delete from Nautobot, push — verify VIP gone from FortiGate...")
    from nautobot_firewall_models.models import NATPolicy, NATPolicyRule

    NATPolicy.objects.filter(name=TEST_NAT_POLICY).delete()
    NATPolicyRule.objects.filter(name=TEST_RULE_NAME).delete()
    if not _push():
        _cleanup()
        return
    rec = _verify_vip_on_fortigate()
    if rec is not None:
        print(f"    ✗ VIP {TEST_VIP!r} STILL on FortiGate after delete-push")
        _cleanup()
        return
    print(f"    ✓ VIP {TEST_VIP!r} gone from FortiGate")

    print("\n[5/5] Final cleanup...")
    _cleanup()
    print("\n" + "=" * 70)
    print(f"✓ NATPolicyRule CRUD validated end-to-end against live FWF-61E")
    print("=" * 70)
