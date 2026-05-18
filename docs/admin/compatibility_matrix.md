# Compatibility Matrix

The intersection of versions that have been tested or are known to work.

## Supported version triple

| Component | Tested | Pin in your project |
|---|---|---|
| `nautobot` | 3.1.2 | `>=3.1,<4` |
| `nautobot-ssot` | 4.2.2 | `>=4.2,<5` |
| `nautobot-firewall-models` | 3.0.0 | `>=3.0,<4` |
| `fortigate-api` | 2.0.8 | `>=2.0,<3` |
| Python | 3.10, 3.11, 3.12, 3.13 | `>=3.10,<3.14` |

## FortiOS

| FortiOS | Pull (cmdb/* read) | Push (cmdb/* write) | Live status (monitor/*) |
|---|---|---|---|
| 7.x | ✓ live-validated on FWF-61E | ✓ live-validated on FWF-61E | ✓ live-validated on FWF-61E |
| 6.4 | ✓ (fortigate-api upstream verifies against 6.4.14) | ✓ (same) | likely OK; not personally validated |
| 5.6 – 6.2 | likely OK with token auth; not personally validated | likely OK; not personally validated | endpoint shapes may differ |
| 5.4 and earlier | requires username/password auth fallback; some endpoints not yet present | unlikely to work without code changes | unlikely to work |

## Why the upper-bound pins matter

All four primary dependencies declare `<4.0.0` Nautobot pins, so a
single bump to Nautobot 4.x will require coordinated updates across the
whole stack. This integration's `>=3.1,<4` upper bound is conservative;
it will be relaxed once the dependency stack moves.

## Multi-VDOM FortiGates

The integration treats each VDOM as a separate scope: a single Job run
syncs one VDOM. To sync multiple VDOMs:

- Run the Job once per VDOM, varying the `vdom` StringVar (`"root"`,
  `"dmz"`, etc.)
- Each VDOM produces independent name-mangled records in Nautobot
  (`fgt-edge1__root__WEB_SERVERS` vs `fgt-edge1__dmz__WEB_SERVERS`)
- ServiceObjects (composite NK) collapse across VDOMs by design — if
  both VDOMs define service `HTTP TCP/80`, they share one Nautobot row

## Multi-device FortiGate fleets

One `ExternalIntegration` per device. The hostname segment of mangled
names provides cross-device scoping for all kinds except `ServiceObject`
(which shares globally).

**Hostname constraint**: the `ExternalIntegration.name` cannot contain
the `__` separator. Use hyphens or dots instead (`fgt-edge1.lab` is fine;
`fgt__edge1` is not).

## Known incompatibilities

| Issue | Workaround |
|---|---|
| `nautobot-firewall-models` `validate_port` validator crashes with `KeyError: 'i'` on space-separated ports | Integration pre-normalizes spaces to commas; no operator action needed |
| `nautobot-firewall-models` `protect_on_delete` signal blocks PolicyRule deletion while attached to a Policy | Integration's `delete()` unlinks from parent Policy first; no operator action needed |
| `fortigate-api` username/password mode runs `POST /logincheck` on every request | Integration wraps calls in `with build_client(ext) as fgt:` to maintain one session per sync |
| Some FortiAP managed-device payloads include `wtp-id` instead of `serial` | Integration falls back to `wtp-id` when `serial` is missing |
