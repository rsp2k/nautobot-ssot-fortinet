# v2026.05.18.1 — Wireless push + policy/NAT push (UPDATE/DELETE)

Same-day follow-up to v1.0 — extends the push direction across wireless, policy, and NAT.

## Added

### Wireless push Job: `Nautobot → FortiGate (wireless)`

A fifth Job appears in `/extras/jobs/`. Pushes:

- **`WirelessNetwork` (VAP)** — full create/update/delete via `cmdb/wireless-controller/vap`. SSID, security mode, broadcast, enabled state, and description all round-trip.
- **`RadioProfile`** — **update-only** via FortiOS partial wtp-profile updates (`wtp-profile.radio-N` payload). Operators can change band, channels, and TX power without re-creating the parent profile.

### Policy push (in the existing firewall push Job)

`PolicyRule` **update + delete**. The most common operator workflow is now supported end-to-end:

1. Pull a FortiGate's policies into Nautobot.
2. Edit a policy's allowed addresses/services/action/log in Nautobot's UI.
3. Run the **Nautobot → FortiGate (firewall)** Job (dry-run first!).
4. The change is applied on the device.

The `policyid` is parsed from the mangled DiffSync name suffix (`<host>__<vdom>__rule_<N>`), so the FortiGate update is unambiguous about which policy is changing.

### NAT push (in the existing firewall push Job)

`NATPolicyRule` **update + delete** via FortiOS VIP partial-update + delete. The push resolves the synthesized `vip_*_mapped` AddressObject back to its IP value for the `mappedip[].range` payload — completing the round-trip with the v1.0 VIP synthesis logic.

## Mapping additions

| Direction | Table | Purpose |
|---|---|---|
| Nautobot → FortiOS | `NAUTOBOT_AUTH_TO_FORTIOS_SECURITY` | WirelessNetworkAuthenticationChoices → `vap.security` |
| Nautobot → FortiOS | `NAUTOBOT_ACTION_TO_FORTIOS` | firewall-models action → FortiOS policy action; handles the `drop` vs `deny` asymmetry |

## Deferred to v2.1

These three CREATE paths require either an interface name (`srcintf` / `dstintf` / `extintf`) that isn't yet stored as a structured DiffSync attr, or multi-radio context that isn't expressible at the per-radio level:

- **PolicyRule create from scratch** — operators must create the policy on the FortiGate UI first, then pull into Nautobot.
- **NATPolicyRule (VIP) create from scratch** — same `extintf` issue.
- **wtp-profile create** — needs full multi-radio + platform-mode context.

The workaround is identical for all three: create the parent object on the FortiGate's web UI, then pull to populate Nautobot. Updates and deletes from Nautobot work normally after that.

## Tests + verification

- 174 unit tests still passing in <1s
- `ruff check + ruff format`: clean
- Live-validated against the same FortiWiFi-61E used for v1.0 (push patterns reuse the v1.0 client + adapter machinery proven against real hardware)
