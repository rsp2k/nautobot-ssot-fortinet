# v2026.05.18.5 — Policy + NAT push live-validated; round-trip stability (v2.4)

Direct follow-up to v2.4's "actually-works edition" retrospective. The
v2.0/v2.1 claims that Policy and NAT push worked were untested — the
buggy `.get(uid=...)` verification pattern produced false positives.
This release fills that gap with real end-to-end tests AND fixes the
two round-trip stability bugs they surfaced.

## What changed

### Two new end-to-end live tests

In `development/scripts/`:

| Script | Validates | Run via |
|---|---|---|
| `e2e_push_policy.py` | PolicyRule CRUD (create + update log toggle + delete) | `make -C development e2e-push-policy` |
| `e2e_push_nat.py` | NATPolicyRule CRUD with synth address round-trip | `make -C development e2e-push-nat` |

Each script: cleanup → inject Nautobot prereqs → push CREATE → verify
on device → modify → push UPDATE → verify → remove from Nautobot →
push DELETE → verify gone → final cleanup. Unconditional cleanup on
every exit path so re-runs are deterministic.

Both passed against a live FortiWiFi-61E running FortiOS 7.0.14.

### Round-trip stability fix #1: `/32` ipmask → ipaddress on pull

FortiOS has no separate "host" address type — IPv4 host IPs are always
stored as `type=ipmask, subnet='IP 255.255.255.255'`. Pre-v2.5 our pull
adapter classified this as `ipmask` with value `'IP/32'`, but the push
side maps DiffSync `ipaddress` back to FortiOS `ipmask /32`. Result:
asymmetric round-trip → phantom diff on every push.

```python
# Pre-v2.5 (asymmetric):
# pull:  type=ipmask, subnet='1.2.3.4 255.255.255.255' → ipmask / '1.2.3.4/32'
# push:  ipaddress / '1.2.3.4'                        → type=ipmask, subnet='1.2.3.4 255.255.255.255'
# Diff on next pull: source(ipaddress/1.2.3.4) ≠ target(ipmask/1.2.3.4/32) → phantom update

# v2.5 (symmetric):
# pull:  type=ipmask, subnet='1.2.3.4 255.255.255.255' → ipaddress / '1.2.3.4'
# push:  ipaddress / '1.2.3.4'                        → type=ipmask, subnet='1.2.3.4 255.255.255.255'
# Round-trip converges; no phantom diff.
```

This also aligns with Nautobot's `IPAddress` semantic for host IPs
(operators see real `IPAddress` records in the UI for /32s, not /32
`Prefix` records).

### Round-trip stability fix #2: `strip_pull_annotations()`

The pull adapter appends machine-generated annotations like
`[srcintf=lan dstintf=wan1]`, `[extintf=wan1]`, and
`[portforward TCP 80 -> 8080]` to descriptions. Pre-v2.5, when these
descriptions were pushed back as FortiOS `comment` fields, the next
pull would see the annotation in the comment AND re-append it →
duplication on every cycle:

```
push 1:  "VIP for app [extintf=wan1]"
pull 2:  "VIP for app [extintf=wan1] [extintf=wan1]"
push 3:  "VIP for app [extintf=wan1] [extintf=wan1] [extintf=wan1]"
```

New `strip_pull_annotations()` helper in `utils.fortios` removes the
exact machine-generated shapes before re-appending. Operator-added
brackets (`[CHANGE-1234]`, etc.) are preserved because the annotation
keys (`srcintf`, `dstintf`, `extintf`, `portforward`) are extremely
unlikely to appear in human-written comments.

## Known minor cosmetic — clears on first pull after upgrade

Operators upgrading with existing Nautobot `AddressObject` records that
were loaded under the pre-v2.5 `ipmask /32` classification will see a
one-time phantom diff for those records (target side now reports
`ipaddress`, source ORM still has `ipmask` from the old load).

**Run the pull Job once after upgrading** to migrate ORM records to
the new classification. After that, push diffs are stable.

## Findings deferred to v2.6+ design

**NAT update via address-value-change doesn't propagate.** If an
operator edits the IP of an existing `vip_*_mapped` `AddressObject`,
the rule's `translated_destination_addresses` M2M still references the
same record by name → no rule-level diff → no `vip.update()` call.

Architecturally clean workaround today: point the rule at a different
`AddressObject` record (the e2e_push_nat.py test does exactly this).

Open design question: should the NAT rule's diff fingerprint the
resolved IP *values* in addition to the M2M names? That would make
"edit the IP of the existing address" automatically trigger a VIP
mappedip update. Tradeoff: extra round-trip computation on every
diff vs. operator ergonomics. Punted to v2.6.

## Tests

- **201 unit tests** (was 193 in v2.4)
- +8 for `strip_pull_annotations` (each annotation pattern + operator
  bracket preservation + idempotency)
- 1 corrected: `test_host_via_32_mask_becomes_slash_32` →
  `test_host_via_32_mask_becomes_ipaddress` (the old name was
  asserting the round-trip-breaking behavior we just fixed)

## Upgrade from v2026.05.18.4

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
# Recommended: run the pull Job once to migrate any pre-v2.5 /32
# AddressObject records to the new ipaddress classification.
```

No new Jobs. No schema changes. No DiffSync attr changes.
