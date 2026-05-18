# v2026.05.18.7 — Remaining CRUD live-validated; DELETE status checking (v2.6)

Closes the "every push CRUD path has a focused live e2e test" gap from
the v2.5 audit. Five new e2e scripts plus DELETE status checking on
all seven write callsites.

## Live-validation matrix (end of v2.7)

| Path | CREATE | UPDATE | DELETE |
|---|---|---|---|
| AddressObject | ✅ live | ✅ live | ✅ live |
| AddressObjectGroup | ✅ live | ✅ live (M2M) | ✅ live |
| ServiceObject | ✅ live | ✅ live | ✅ live |
| ServiceObjectGroup | ✅ live | ✅ live (M2M) | ✅ live |
| WirelessNetwork (VAP) | ✅ live | ✅ live | ⚠ FortiOS REST limitation |
| PolicyRule | ✅ live | ✅ live | ✅ live |
| NATPolicyRule | ✅ live | ✅ live (+ value-change v2.6) | ✅ live |
| RadioProfile / wtp-profile | ✅ live (aggregation) | n/a | n/a (by design) |

Run any of these yourself with `make -C development e2e-push-<model>`,
or all of them in sequence with `make -C development e2e-push-all`.

## DELETE status checking

v2.4 wrapped CREATE / UPDATE with `check_fortios_response()`. v2.7
closes the gap for the remaining 7 delete callsites. This is what
surfaced the VAP REST limitation below — pre-v2.7, the VAP DELETE
silently returned HTTP 500 and our code logged "Deleted successfully."

## Known FortiOS quirks (surfaced this release)

### VAP DELETE via REST is fundamentally broken

When you create a VAP on FortiOS, it auto-creates a dependent
"quarantine interface" named `wqtn.<vlanid>.<truncated-vap-name>`.
This causes a circular dependency:

```
DELETE /api/v2/cmdb/wireless-controller/vap/MY_VAP
→ http=500, error -23, cli_error="Vap quarantine interface
  wqtn.21.MY_VAP is in use."

DELETE /api/v2/cmdb/system/interface/wqtn.21.MY_VAP
→ http=500, error -23, cli_error="The entry is used by other 1
  entries"
```

Neither can be deleted while the other exists. **The FortiGate web
UI's VAP delete wizard handles the dependency teardown internally** —
operators must use the UI for VAP removal. REST does not expose this
capability cleanly.

Implications for this integration:
- `e2e_push_vap.py`'s DELETE phase is documented and intentionally
  skipped.
- Operators running push Jobs with `delete_records_missing_from_source=
  True` against wireless config will see `FortiOSAPIError` for VAPs
  that were removed from Nautobot — they'll need to delete those VAPs
  on the FortiGate web UI separately.

### `internal` interface is a switch parent, not a policy endpoint

On FortiWiFi/FortiGate-D series devices, the `internal` interface is
a hardware switch (with members `internal1` through `internal7`).
Using `internal` directly in a policy's `srcintf` or `dstintf` returns
FortiOS error -651: "node_check_object fail! for name internal."

Use `internal1`-`internal7` (or define a zone) when populating
`PolicyRule.source_interfaces` / `destination_interfaces`. Hit during
e2e_push_policy validation in v2.5.

## Upgrade from v2026.05.18.6

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

No schema changes. No new Jobs.

**Behavior change worth noting:** previously-silent delete failures
will now raise `FortiOSAPIError`. If a push Job suddenly fails on a
delete that pre-v2.7 logged "Deleted successfully," that failure was
already there — v2.7 just makes it visible. The exception message
includes the FortiOS error code and `cli_error` text; investigate
those and either fix the FortiGate state manually or scope the push
to omit the problematic delete.

## What's next

The infrastructure for catching this class of issue is now in place
across every CRUD path. Documentation work (operator-facing
troubleshooting page covering the FortiOS quirks above, with
screenshots from the Nautobot UI) is queued for v2.8.
