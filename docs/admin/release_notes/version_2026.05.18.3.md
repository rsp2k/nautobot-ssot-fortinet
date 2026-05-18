# v2026.05.18.3 — wtp-profile CREATE via sibling aggregation (v2.2)

Fourth release today. Closes the v2.1 deferral — push is now full-CRUD
across every model.

## What changed

### `FortiGateRadioProfile.create()` aggregates siblings from the source

Pre-v2.2 the model could only *update* a `radio-N` subfield on an
already-existing wtp-profile. If the parent wtp-profile didn't exist on
the FortiGate, push emitted a warning and skipped. Now:

| Condition | Pre-v2.2 | v2.2 |
|---|---|---|
| wtp-profile exists on target | partial `radio-N` PUT | unchanged |
| wtp-profile absent + this is the first sibling | warning + skip | **POST whole profile with all sibling radios** |
| wtp-profile absent + this is a later sibling | warning + skip | per-radio update against the just-created profile |

The mechanism: the push Job stashes `target.source_adapter = source`
right before `execute_sync()` runs. When DiffSync's diff engine calls
`FortiGateRadioProfile.create()`, the model checks the target store for
existing siblings (same `original_profile_name`). If none, it pivots to
`source.get_all(cls)` to collect ALL RadioProfiles for that profile
name, builds one combined wtp-profile POST payload with every `radio-N`
populated, and creates the whole thing in a single FortiOS call.

### Default `platform-mode` on create

The wtp-profile container has a `platform-mode` field (FortiAP-tunnel-mode,
mesh, bridge, local-flex) with no per-radio equivalent in Nautobot. We
default new profiles to `FortiAP-tunnel-mode` — the most common managed-AP
deployment. Override on the FortiGate UI after first push if you're
running anything else; the value sticks across subsequent per-radio updates.

### New workflow: author wireless profiles in Nautobot

```
Nautobot UI:
  Create RadioProfile(name='guest-radio1', original_profile_name='guest',
                      radio_index=1, frequency='2.4GHz', ...)
  Create RadioProfile(name='guest-radio2', original_profile_name='guest',
                      radio_index=2, frequency='5GHz', ...)
      ↓
Run "Nautobot → FortiGate (wireless)" Job (dry-run first!)
      ↓
FortiGate has new wtp-profile 'guest' with radio-1 + radio-2 populated.
Verify on FortiGate web UI under WiFi Controller → FortiAP Profiles.
```

Pre-v2.2 workaround was "create the empty wtp-profile shell on the FortiGate
UI first, then push from Nautobot." No longer needed.

### Tests

- 188 unit tests (was 183 in v2.1). +5 for `FortiGateRadioProfile.create()`
  covering: missing `original_profile_name`, target-sibling-exists update
  path, source aggregation with 2 radios, source aggregation with 1 radio,
  missing-source-adapter warn+skip.
- All ruff lint + format clean.

## Still in scope, intentionally limited

- **Single-radio delete is still a no-op.** FortiOS doesn't have a "remove
  one radio from a wtp-profile" endpoint — you either delete the whole
  profile or you don't. Delete the wtp-profile on the FortiGate UI if you
  need that.
- **platform-mode isn't tracked in Nautobot.** We never read it back on
  pull and we never push it on update — operators control it directly on
  the FortiGate UI.

## Upgrade from v2026.05.18.2

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

No new Jobs (the existing 5 are unchanged). No schema changes. No new
DiffSync attrs. The RadioProfile push path is simply more capable now.
