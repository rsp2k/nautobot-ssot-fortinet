# v2026.05.18.2 — PolicyRule + NATPolicyRule CREATE (v2.1)

Third release today. Removes the v2.0 deferral of CREATE for policies and NAT VIPs.

## What changed

### First-class interface attrs on PolicyRule / NATPolicyRule

- `PolicyRule.source_interfaces` and `destination_interfaces` (lists of FortiOS interface/zone names)
- `NATPolicyRule.external_interface` (single FortiOS interface name)

The pull side populates these from raw FortiOS data; the Nautobot adapter parses them back from the description's `[srcintf=X dstintf=Y]` / `[extintf=Z]` annotation. The description annotation **doubles as human-readable UI text AND structured persistence** — no schema migration needed.

### CREATE paths enabled

| Push action | Pre-v2.1 | v2.1 |
|---|---|---|
| `PolicyRule.create` | warning + skip | **POST to FortiGate** with srcintf/dstintf from structured attrs |
| `NATPolicyRule.create` | warning + skip | **POST VIP** with extintf + reconstructed extip/mappedip |
| `PolicyRule.update` | ✓ already worked | ✓ unchanged |
| `PolicyRule.delete` | ✓ already worked | ✓ unchanged |
| `NATPolicyRule.update` | ✓ already worked | ✓ unchanged |
| `NATPolicyRule.delete` | ✓ already worked | ✓ unchanged |

### New workflow: author policies in Nautobot

```
Nautobot UI:
  Create PolicyRule(source_addresses=[A], destination_addresses=[B],
                    source_interfaces=['lan'], destination_interfaces=['wan1'],
                    action='allow', log=True, ...)
      ↓
Run "Nautobot → FortiGate (firewall)" Job (dry-run first!)
      ↓
FortiGate has the new policy. Verify on FortiGate web UI.
```

Pre-v2.1 workaround was "create the policy on the FortiGate UI first, then pull." No longer needed.

### Tests

- 183 unit tests (was 174 in v2.0). +9 for `parse_intf_annotation` covering single/multi-value, edge cases, round-trip with the pull-side format.
- All ruff/format clean.

## Still deferred to v2.2

- **wtp-profile create from a single RadioProfile** — multi-radio aggregation that isn't expressible at the per-radio DiffSync level.

## Upgrade from v2026.05.18.1

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

No new Jobs (the existing 5 are unchanged). The PolicyRule and NATPolicyRule push paths are simply more capable now.
