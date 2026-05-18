# Changelog

This project uses [CalVer](https://calver.org/) — versions are `YYYY.MM.DD`
representing the date of release. Same-day fixes use `YYYY.MM.DD.N`.

## 2026.05.18.13.6 — Named-address-object route resolution + two FortiOS shape gotchas

Closes the "v3.1 deliberately skips dstaddr-form routes" deferral from
the v3.1 release notes. Operators (including Kevin's prod sync) with
routes that reference named address objects via FortiOS's ``dstaddr``
field now sync correctly. The resolver is self-contained (one extra
REST call per unique address name per sync, cached) — no cross-Job
ordering dependency with the firewall pull.

### What's new

- ``fortios_route_destination_cidr()`` gains an optional ``resolver``
  callback. When provided AND the route uses ``dstaddr``, the callback
  is invoked with the address name and expected to return a CIDR string
  (or None if the address can't be represented as a single CIDR).
- ``FortiGateDevicesAdapter._resolve_address_cidr()`` is the production
  implementation: hits ``cmdb/firewall/address`` filtered by name,
  reads ``type`` + ``subnet``, returns CIDR for ipmask-type addresses.
  Per-sync cache so repeat references = 1 REST call.
- New pure helper ``_normalize_dstaddr_names()`` for the two FortiOS
  dstaddr shapes (see below).

### Two FortiOS shape gotchas surfaced during live validation

Both caught while injecting a dstaddr-form route on the dev FortiWiFi-61E
to validate the new resolver path end-to-end.

**1. ``dstaddr`` returned as plain string, not list-of-dict (FortiOS 7.0.x).**

Documented examples and FortiOS 7.2+ return ``dstaddr`` as
``[{"name": "X"}]``. FortiOS 7.0.14 on the FortiWiFi-61E returns it
as the bare string ``"X"``. Both shapes appear in the wild; both are
handled uniformly via ``_normalize_dstaddr_names()``.

**2. ``dst="0.0.0.0 0.0.0.0"`` is a PLACEHOLDER, not a default route,
when ``dstaddr`` is populated.**

When operators create a dstaddr-based route, FortiOS sets ``dst`` to
the all-zeros sentinel internally — but the *real* destination is what
``dstaddr`` resolves to. Pre-v3.2.6 our code checked ``dst`` first
and would have misread these as default routes (``0.0.0.0/0``).
**Precedence rule flipped**: dstaddr wins when populated, regardless
of dst value.

### Live-validation result

Against fgt-dev (FortiWiFi-61E / FortiOS 7.0.14) with a real
dstaddr-form route:

```
Route seq=9002, FortiOS state: dst="0.0.0.0 0.0.0.0", dstaddr="ssot_test_dstaddr"
Address ssot_test_dstaddr: type=ipmask, subnet="192.0.2.0 255.255.255.0"

→ Nautobot FortinetStaticRoute:
  seq_num=9002, destination="192.0.2.0/24", gateway="192.168.1.1", dev=wan2
```

Pre-v3.2.6 this would have synced as destination=``0.0.0.0/0`` (wrong
— would have been treated as a default-route override).

### FortiOS REST limitations (documented, not bugs)

Creating dstaddr-form routes VIA REST is gated by two FortiOS REST
quirks discovered during the live-injection probe:

1. **`error -173` (XSS check)** rejects POST/PUT bodies containing
   ``dstaddr: [{"name": "..."}]`` — the `{ ` character pattern
   triggers FortiOS's XSS input filter.
2. **`error -3` (entry not found)** rejects route create unless the
   referenced AddressObject has ``allow-routing: enable``.

These only affect CREATE-via-REST (which v3.2.6 doesn't do). Routes
created via FortiOS web UI or CLI are unaffected and read correctly
through v3.2.6's resolver path. The future static-route push feature
(v3.3+) will document these for operators.

### What's deliberately NOT supported

- **Multi-entry dstaddr** — FortiOS allows ``dstaddr=[{name: A}, {name: B}]``
  semantically meaning "this route matches any of these addresses".
  Mapping to N Nautobot Route records sharing one seq_num would violate
  our ``(device, vdom, seq_num)`` unique constraint. Skipped with a
  clear warning so operators see exactly what was dropped.
- **dstaddr → fqdn/iprange/mac/dynamic/geography addresses** — no
  clean single-CIDR mapping for a route destination. Skipped with the
  type-specific warning text.

### Tests

- **269 unit tests** (was 263 in v3.2.5). +6 covering:
  - dstaddr string form (FortiOS 7.0.x shape) resolves via callback
  - dstaddr list-of-dict form (FortiOS 7.2+ shape) resolves via callback
  - dstaddr-precedence-over-placeholder-dst (the live-caught bug)
  - multi-entry dstaddr skipped
  - resolver returns None propagates (skip)
  - ``_normalize_dstaddr_names()`` pure helper for the two shapes
- Live-validated end-to-end against fgt-dev's actual FortiOS 7.0.14
  RUNNING DSTADDR ROUTE — not just synthetic fixture data.

### Backwards-compat note

The new ``resolver=None`` default to ``fortios_route_destination_cidr()``
preserves pre-v3.2.6 behavior (callers without a resolver still skip
dstaddr-form routes). Any external code calling the helper without a
resolver kwarg keeps working.

### Upgrade

```bash
pip install --upgrade nautobot-ssot-fortinet  # = 2026.5.18.13.6
sudo systemctl restart nautobot nautobot-worker
```

Re-run the Devices Job once to pick up any previously-skipped
dstaddr-form routes. Each will get its own FortinetStaticRoute record
with the resolved CIDR.

## 2026.05.18.13.5 — Device.serial extraction fix (closes v3.0 carryover bug)

The known-since-v3.0 empty-serial bug is fixed. Every Device created by
the SSoT pull Job now gets its real FortiOS serial number populated.

### Two-failure-mode bug

The v3.0 `_get_fortios_serial()` used:
```python
raw = self.client.fortigate.get_result("/api/v2/cmdb/system/interface?count=1")
return raw.get("serial", "") or ""
```

Both halves were wrong:

1. **`get_result()` strips the response envelope** — but `serial` lives
   IN the envelope (alongside `version`, `build`, `vdom`), not in
   `results`. So even if the call had succeeded, `raw["serial"]` would
   have been `KeyError` on a dict that didn't contain it.
2. **`get_result()` crashes on `system/interface`** — its implementation
   does `dict(data.get("results"))`, and `system/interface` returns a
   LIST of interface dicts. `dict(list_of_dicts_with_many_keys)` raises
   `ValueError: dictionary update sequence element #0 has length N; 2
   is required`. The `BLE001`-suppressed `except` swallowed the crash
   silently, returning `""`.

Result: **every Device created by v3.0, v3.1, v3.2.0–v3.2.4 had `serial=""`.**

### Fix

Hit `system/global` directly via raw `.get()` (NOT `.get_result()`),
read the envelope's `serial` field:

```python
response = self.client.fortigate.get("/api/v2/cmdb/system/global")
if getattr(response, "status_code", None) != 200:
    return ""
envelope = response.json()
return envelope.get("serial", "") or ""
```

`system/global` is the right endpoint because:
- It always exists on every FortiOS version we support (6.x–7.x)
- Its `results` is a single dict (not a list)
- Its envelope includes `serial`, `version`, `build` — everything we'd
  want for Device identity

### Live-validated against the dev FortiWiFi-61E

After running the Devices Job on v3.2.5:
```
Device.serial = '<populated with real FortiOS serial>'
```

Pre-fix this was always `''`.

### Tests

- **254 unit tests** (was 250 in v3.2.4). +4 covering:
  - serial extracted from envelope happy path
  - envelope without serial key → returns `''`
  - non-200 status → returns `''`
  - HTTP call raises → returns `''` and doesn't crash the load
- Tests use a placeholder serial format (`FWF61E0000000000`) — never
  real device data.

### Backwards-compat note

If you upgrade from any v3.0 / v3.1 / v3.2.0–v3.2.4 with an existing
Nautobot Device record whose serial is empty, the next Devices Job
run will populate it. The diff will show
`update fortigate_device serial: '' → '<actual-serial>'` — that's
expected, not drift.

### Upgrade

```bash
pip install --upgrade nautobot-ssot-fortinet  # = 2026.5.18.13.5
sudo systemctl restart nautobot nautobot-worker
```

Re-run the `FortiGate -> Nautobot (device + interfaces)` Job to
refresh `Device.serial` for any previously-empty records.

## 2026.05.18.13.4 — Audit polish; first version pushed to PyPI (v3.2 series)

Strict follow-up to v3.2.3. The CHANGELOG entry for v3.2.3 *described*
what was removed by quoting the original strings verbatim — a small
audit-loop oversight. This release sanitizes the audit description
itself so the CHANGELOG meta-commentary doesn't reintroduce the same
strings. Net change: a few CHANGELOG paragraphs rewritten to refer to
the sanitized values abstractly ("operator's personal device name"
rather than the actual name). No code, schema, or behavior change.

**v3.2.4 is the first version that actually ships to PyPI.** v3.1
through v3.2.3 exist only as local git history + GitHub tags.

## 2026.05.18.13.3 — Pre-publish PII audit findings (v3.2 series)

Pre-publish Stage 2 audit (per CLAUDE.md python.md rules) caught two
PII-class issues before the first push to PyPI. **No `uv publish` was
ever called against v3.1, v3.2, v3.2.1, or v3.2.2** — those exist only
as git tags + local commits. v3.2.3 is the first version that will
land on PyPI.

### What the audit caught

1. **Claude-scratch `.tmp` backup file shipping in the sdist.**
   The existing `[tool.hatch.build.targets.sdist] exclude` used an
   exact match for the canonical filename — `.tmp.*` variants slipped
   through. One ~9 KB file containing operator-private project notes
   was caught in the v3.2.2 sdist. Removed from repo + added a glob
   pattern to the exclude list so future scratch files can't escape.
2. **Operator-name leak in a doctest example.** The
   `fortios_placeholder_fqdn` docstring used a verbatim personal-device
   name from an operator's prod log as the "spaces → dashes" example.
   Replaced with a generic placeholder ("Lab IoT Device"). Matching
   unit test updated. Doctest still demonstrates identical behavior.

### Structural defense added

```toml
[tool.hatch.build.targets.sdist]
exclude = [
    "CLAUDE.md",
    # Glob pattern added in v3.2.3 to catch Claude scratch backup files.
    "CLAUDE.md.tmp.*",
    "/artifacts/",   # container-side test artifacts (screenshots, logs)
    ...
]
```

### Lesson

CLAUDE.md python.md says it plainly: "the Stage 2 unpacked-sdist grep
is the *authoritative* check before `uv publish`. Empty result = safe
to publish. Anything else = scrub the source, rebuild, re-audit."

This release IS the worked example of that rule firing. The audit
caught both issues; the fixes shipped before any artifact reached
PyPI. Total cost of the catch: one version bump, one rebuild cycle.
Cost if missed: yanked-but-immortal PyPI artifact + email to
admin@pypi.org for removal.

### Tests

- **250 unit tests** still passing.
- Sanitized doctest still demonstrates the same sanitization behavior
  (spaces → dashes); just with a non-PII example string.

### Upgrade

```bash
pip install --upgrade nautobot-ssot-fortinet  # = 2026.5.18.13.3
sudo systemctl restart nautobot nautobot-worker
```

No migration, no schema change. Includes all v3.1 + v3.2 + v3.2.1 +
v3.2.2 capabilities and fixes since none of those shipped to PyPI.

## 2026.05.18.13.2 — HOTFIX: blackhole field misclassification (v3.1 regression)

**Bug present in v3.1 (2026.05.18.12), v3.2 (2026.05.18.13), and v3.2.1
(2026.05.18.13.1).** Surfaced when running the Devices Job against the
dev FortiWiFi-61E with a real DHCP-bound default route.

### Root cause

FortiOS returns the `blackhole` field as a **string** (`"enable"` or
`"disable"`), not a Python bool. The v3.1 adapter did:

```python
blackhole = bool(raw.get("blackhole", False)) or raw.get("blackhole") == "enable"
```

The first clause `bool("disable") == True` (non-empty string is truthy),
so **every** non-blackhole route was misclassified as blackhole. The
subsequent gateway normalization then wiped the gateway to `""` since
"blackhole" routes don't have one.

### Live evidence from the dev FortiWiFi-61E

Pre-v3.2.2 sync output:

```
✓ seq=9001  203.0.113.0/24  via BLACKHOLE  dev=wan2  distance=10
```

Post-v3.2.2 (correct):

```
✓ seq=9001  203.0.113.0/24  via 192.168.1.1  dev=wan2  distance=10
```

### Fix

Match FortiOS's actual string shape explicitly, accepting `True` for
forward-compat:

```python
bh_raw = raw.get("blackhole")
blackhole = bh_raw == "enable" or bh_raw is True
```

### New regression-guard test

`test_non_blackhole_route_blackhole_field_disable_string` in
`tests/test_adapters_devices.py` uses the exact FortiOS shape that
broke us (`"blackhole": "disable"`) and asserts the route surfaces with
`blackhole=False` + gateway preserved. **250 unit tests pass** (was 249).

### Why our existing tests missed it

The existing `test_blackhole_route_has_empty_gateway` used
`"blackhole": "enable"` (the positive case). The negative-case shape
was never tested — and FortiOS only returns `"disable"` for routes you
actually want preserved, which the fixture didn't have.

### Live-verified on real hardware

Inject script + revalidate cycle confirmed the fix against the dev
FortiWiFi-61E (FortiOS 7.0.14): VLAN sub-interface + static route both
sync end-to-end through the Job → write to FortinetStaticRoute Django
model → render correctly in the Nautobot UI. Browser-rendered table
shows `Blackhole: ✘` for the non-blackhole route.

### Also in this release

- New helper scripts in `development/scripts/`:
  - `e2e_v31_inject_testdata.py` — injects a test VLAN + route on
    fgt-dev (used to reproduce the bug)
  - `e2e_v32_hw_validate.py` — full v3.1/v3.2 live validation against
    fgt-dev; prints HOPOPT services, placeholder addresses, VLAN
    sub-interfaces, and routes

### Lesson — caught by integration testing, not unit testing

Our unit test fixtures used FortiOS's positive case (`"enable"`) but
not the negative case (`"disable"`). The mocked Connector accepted
either silently. Running against a real FortiGate is what surfaced the
bug — same pattern as v2.8 (Kevin's `Job.run()` discovery) and v3.2.1
(dev-stack navigation crash). **Integration validation against real
Nautobot AND real hardware is the only thing that exercises the full
field-shape contract.**

### Upgrade from any v3.1 / v3.2 / v3.2.1

```bash
pip install --upgrade nautobot-ssot-fortinet
sudo systemctl restart nautobot nautobot-worker
```

No migration. **Operators who synced routes between v3.1 and v3.2.1
will have all-`blackhole=True` records in `FortinetStaticRoute` with
empty gateways.** After upgrading, re-run the Devices Job to refresh:
the blackhole flag and gateway will be corrected on the next sync.

## 2026.05.18.13.1 — HOTFIX: navigation.py NavMenuGroup type error (v3.1 regression)

**Crash bug present in v3.1 (2026.05.18.12) and the just-released v3.2
(2026.05.18.13).** Any deployment that loaded the app with v3.1+ saw
the Nautobot worker container restart-loop with:

```
TypeError: All groups defined in a tab must be an instance of NavMenuGroup
  File "src/nautobot_ssot_fortinet/navigation.py", line 8, in <module>
    NavMenuTab(...)
```

Caught when actually starting the dev stack and observing the worker
crash. Not caught by unit tests because `conftest.py` stubs out
`nautobot.apps.ui` to a MagicMock — type validation never fires.

### Root cause

`v3.1`'s `navigation.py` passed bare dicts to `NavMenuTab(groups=...)`,
patterning on the `nautobot_ssot.integrations.itential` reference —
but that reference uses a different module (`NavMenuItem` at the top
level, not wrapped in `NavMenuTab`). The actual Nautobot 3.x API
requires `NavMenuGroup(...)` instances when nesting under `NavMenuTab`.

### Fix

One-line import + 3-line refactor in `src/nautobot_ssot_fortinet/navigation.py`:

```python
from nautobot.apps.ui import (
    NavMenuAddButton, NavMenuGroup, NavMenuItem, NavMenuTab,  # +NavMenuGroup
)

menu_items = (
    NavMenuTab(name="Plugins", groups=(
        NavMenuGroup(name="Fortinet SSoT", weight=1000, items=(...))  # was a bare dict
    ))
)
```

### Why this is its own release

v3.2 (2026.05.18.13) was already committed before the dev-stack
validation surfaced this. Per the project's hotfix convention
(`YYYY.MM.DD.N.M`), this ships as a post-release of v3.2 rather than
contaminating the v3.2 changelog with an unrelated bug fix.

### Live-verified

Restarted the dev container with the fix, worker came up healthy
within 20 seconds, migration applied cleanly, model queries pass,
URL routing resolves.

### Lesson — should we have caught this earlier?

The unit-test conftest stubs `nautobot.apps.ui` to MagicMock so any
constructor call accepts any args without validation. Adding a real
import check for navigation/views/urls modules would catch this class
of bug. Tracked as a v3.3 backlog item.

### Upgrade from any v3.1 / v3.2

```bash
pip install --upgrade nautobot-ssot-fortinet
sudo systemctl restart nautobot nautobot-worker
```

No migration, no Job changes. Operators who deployed v3.1 or v3.2 and
saw worker restart-loop should upgrade immediately.

## 2026.05.18.13 — FortiOS shape coverage from Kevin's prod sync (v3.2)

Same-day follow-up to v3.1, driven by **operator-reported gaps** when
Kevin Mueller ran the v3.1 firewall pull Job against his production
FortiWiFi-61E (FortiOS 7.2). The sync completed successfully but logged
**80+ warnings** about policies dropping their service references and
~15 about address objects being skipped. All of those were caused by
two real-world FortiOS shapes the integration didn't model.

### The cascade we fixed

Kevin's prod config contains a FortiOS-built-in service named ``ALL``
with shape:

```
edit "ALL"
    set protocol IP    ← no protocol-number; defaults to 0
```

Pre-v3.2 path: ``protocol == "IP"`` + ``protocol-number is None`` →
return ``(None, "")`` → service skipped → **80+ policies referencing
``ALL`` silently lose their service reference**. The cascade was the
symptom; the root cause was one missing protocol-number mapping.

### Service mapping fixes — one fix, 80+ policies un-broken

- **``protocol IP`` with no number** now maps to ``HOPOPT`` (IANA
  protocol 0) instead of being skipped. HOPOPT is the IPv6 Hop-by-Hop
  Options header — so rare in real firewall rules that repurposing it
  as the "any IP protocol" sentinel is safe in practice, and lets the
  FortiOS ``ALL`` service round-trip into Nautobot intact.
- **``protocol ALL``** (the FortiOS pseudo-protocol used by the built-in
  ``webproxy`` service and operator-defined proxy services) maps to the
  same ``HOPOPT`` sentinel. Identical operator-facing semantics.

### Address type coverage via ``.fortios.invalid`` placeholders

Three FortiOS address types had no clean Nautobot home:

| FortiOS type | Kevin's count | Use case |
|---|---|---|
| ``mac`` | 11 | IoT devices identified by MAC address |
| ``dynamic`` | 3 | FortiClient EMS-managed dynamic groups |
| ``geography`` | 1 | Country-code address objects |

``nautobot-firewall-models.AddressObject`` requires exactly one of
``fqdn`` / ``ip_range`` / ``ip_address`` / ``prefix``. There's no MAC
field, no placeholder slot. v3.2 mints **placeholder FQDNs** under the
RFC 2606-reserved ``.fortios.invalid`` TLD, so:

- ``ipcam01`` (mac) → fqdn ``ipcam01.mac.fortios.invalid``
  with description ``[FortiOS MAC: aa:bb:cc:dd:ee:01]``
- ``EMS_ALL_UNKNOWN_CLIENTS`` (dynamic) → fqdn
  ``ems-all-unknown-clients.dynamic.fortios.invalid``
  with description ``[FortiOS dynamic EMS group]``
- ``GEO_RU`` (geography, country=RU) → fqdn ``geo-ru.geo.fortios.invalid``
  with description ``[FortiOS geography: RU]``

``.invalid`` never resolves in real DNS (per RFC 2606), so operators
see immediately that these are sync-time placeholders. The address
becomes referenceable from firewall policies, which un-breaks any
policy that referenced an IoT / EMS / geo address.

### New visibility: unknown address refs in policies

Pre-v3.2 ``split_policy_members()`` silently dropped unknown member
names. The new optional ``unknown_callback`` parameter lets the policy
adapter log every dropped reference, matching the symmetry of the
``Policy 'X' references unknown service 'Y'`` warnings.

### New helper: ``fortios_placeholder_fqdn(category, name)``

Pure function in ``utils/fortios.py``. Sanitizes the input name to a
DNS-safe label (lowercase, ``[a-z0-9-]`` only, ≤63 chars per the
DNS label spec) and returns ``<sanitized>.<category>.fortios.invalid``.

### Tests

- **249 unit tests** (was 231 in v3.1). +18 covering the new mappings:
  ALL/webproxy → HOPOPT, placeholder FQDN sanitization, MAC/dynamic/
  geography address loading, ``unknown_callback`` plumbing.
- Adapter-level fixture extended with ``mac``, ``dynamic``, ``geography``
  records + ``ALL`` and ``webproxy`` services so the end-to-end load
  path is exercised through the same harness as the existing types.
- Two pre-existing tests updated to reflect v3.2 behavior:
  ``test_get_all_returns_consistent_count`` and
  ``test_all_pseudoprotocol_skipped`` (renamed to
  ``test_all_pseudoprotocol_maps_to_hopopt_in_v32``).
- All ruff lint + format clean.

### Backwards-compat note

The ``IP_PROTOCOL_NUMBER_TO_NAME`` map gained a new key (``0``). Any
caller assuming protocol 0 was unmappable will see different behavior
now. The change is **additive** — previously-skipped services are now
emitted, but no service that previously worked has changed behavior.

The ``split_policy_members()`` signature gained an optional keyword arg
``unknown_callback=None`` — calls without it behave exactly as before
(silent drop).

### Upgrade from v2026.05.18.12

```bash
pip install --upgrade nautobot-ssot-fortinet
sudo systemctl restart nautobot nautobot-worker
```

No schema migration (the v3.1 migration is the latest; v3.2 only
touches utility code and the firewall pull adapter). Re-run the
firewall pull Job once to pick up the previously-skipped addresses and
services. Operators will see new ``AddressObject`` records under names
like ``<host>__<vdom>__ipcam01`` with ``.fortios.invalid`` FQDN values
— that's expected, not drift.

## 2026.05.18.12 — VLAN sub-interfaces + Static Routes (v3.1)

Builds on v3.0's Device + Interface sync with two big additive features:
**operator-defined VLAN sub-interfaces** flow through the existing Devices
Job, and **FortiOS static routes** become a first-class Django model in
Nautobot with proper list/detail views.

### New Django model: `FortinetStaticRoute`

A dedicated model representing one FortiOS `router.static` entry. Lives
alongside the existing app models with proper schema, filterset, forms,
table, UI viewset, and navigation menu entry.

| Field | Type | Notes |
|---|---|---|
| `device` | FK(`dcim.Device`) | CASCADE delete. The FortiGate this route lives on. |
| `vdom` | CharField(32) | Default `"root"`. Routes are vdom-scoped. |
| `seq_num` | PositiveIntegerField | FortiOS primary key per (device, vdom). |
| `destination` | CharField(43) | CIDR string. Validated. |
| `gateway` | GenericIPAddressField | Null for blackhole. |
| `interface` | FK(`dcim.Interface`) | Null for blackhole/RIB-resolved. SET_NULL on delete. |
| `distance` | PositiveSmallIntegerField | Default 10 (FortiOS default). |
| `priority` | PositiveSmallIntegerField | Default 0. |
| `blackhole` | BooleanField | If True, traffic is silently discarded. |
| `comment` | CharField(255) | Operator-facing description. |

Composite uniqueness: `(device, vdom, seq_num)`.

URL: `/plugins/ssot-fortinet/static-routes/` — list, detail, add, edit,
bulk-edit, and bulk-delete views all work out of the box from the
NautobotUIViewSet pattern.

### VLAN sub-interface sync

Pre-v3.1 the Devices Job dropped every `type=vlan` interface. Now they
flow through to Nautobot as `Interface(type='virtual')` with three new
attrs:

- `parent_interface` (FK) — resolved from FortiOS `interface` field
- `untagged_vlan` (FK to `ipam.VLAN`) — auto-created if missing,
  named `<device>-vlan<vid>`
- `mode` — `"tagged"` for FortiOS VLAN sub-interfaces (the common case)

Filtering policy: name-based skip continues for FortiOS-internal
artifacts. The new helper `is_internal_fortios_interface()` rejects
`wqtn.*` (VAP quarantine), `vap.*` (VAP-tagged switch ports), `ssl.*`
(SSL-VPN root), and `naf.*` (FortiOS 7.4+ name-affinity artifacts).
The type-map flip (`vlan` → `'virtual'` instead of `None`) AND the
name filter ship in the same release — otherwise the first sync would
explode the Interface count with quarantine records.

### New Job form var: `include_static_routes`

The existing `FortiGate → Nautobot (device + interfaces)` Job gains an
opt-in `include_static_routes` BooleanVar, default **True**. Operators
who don't want Nautobot managing route inventory can turn it off; the
Job will skip the `router.static` pull entirely.

### New helpers in `utils.fortios`

- `is_internal_fortios_interface(name)` — name-prefix filter for
  FortiOS-internal artifacts
- `fortios_route_destination_cidr(raw)` — extracts CIDR from a
  `router.static` record. Handles the dotted-mask form
  (`"10.20.0.0 255.255.0.0"`) and the default route (`"0.0.0.0 0.0.0.0"`).
  Returns `None` for the named-address-object form (`dstaddr` field) —
  v3.1 deliberately doesn't resolve those, since the route would
  introduce ordering dependencies between the firewall + device Jobs.
  Caller logs and skips.

### Tests

- **231 unit tests** (was 202 in v3.0). +29 covering the new helpers,
  VLAN extraction, route loading, and the route DiffSync skip logic.
- All ruff lint + format clean.

### Known limitations of v3.1

- **Routes with `dstaddr` (named address object) are skipped on pull.**
  We could resolve them by reading the AddressObject's `subnet` field
  during the route load, but that introduces a hard ordering dependency
  on the firewall Job — defer to a release that handles cross-Job
  dependencies cleanly.
- **Push direction for VLANs / routes is not in scope.** Pull-only,
  same as v3.0. Wrong push to FortiOS routing can blackhole production
  traffic; push requires pre-validation safeguards (separate release).
- **VLAN sub-interface DELETE on Nautobot side cascades to the
  auto-created `ipam.VLAN`** only if no other Interface references it.
  This is Nautobot ORM behavior, not something we control.
- **Static route push (Nautobot → FortiGate)** isn't wired up — there
  is no `FortiGate.cmdb.router.static.create/update/delete` target
  adapter yet. The route table model exists; the inverse Job will
  follow in v3.2 once push-side validation is designed.

### Upgrade from v2026.05.18.11 — SCHEMA MIGRATION REQUIRED

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server migrate nautobot_ssot_fortinet  # NEW — creates FortinetStaticRoute table
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

The `migrate` step is what's different from previous releases — this is
the first release that adds DB-backed models to the app. Running the
upgrade without `migrate` will leave the new "Static Routes" navigation
entry broken (it links to a list view that depends on a table that
doesn't exist yet).

The existing Job (`FortiGate → Nautobot (device + interfaces)`) gains
the `include_static_routes` form var. Default value is True, so existing
saved Job runs will start pulling routes on next execution. To preserve
v3.0 behavior (devices + interfaces only), uncheck the new form var.

## 2026.05.18.11 — Device + Interface sync (v3.0)

The first new capability since the v2.x stability work. **The FortiGate
now appears as a Nautobot `dcim.Device`** with its operator-meaningful
interfaces and IP assignments synced from `system.interface`.

### New Job

`FortiGate -> Nautobot (device + interfaces)` (5th DataSource Job,
6 total Jobs registered).

Form vars (all required except delete-flag):
- **External integration** (ObjectVar) — picks the FortiGate
- **Vdom** (StringVar, default "root") — scope
- **Device type** (ObjectVar) — Nautobot DeviceType, e.g. "FortiWiFi-61E"
- **Role** (ObjectVar) — Nautobot Role, e.g. "Firewall"
- **Location** (ObjectVar) — Nautobot Location
- **Status** (ObjectVar) — Nautobot Status, typically "Active"
- **Delete records missing from source** (BooleanVar, default False)

Operators must pre-create the DeviceType / Role / Location / Status
records (Nautobot best practice — same pattern the wireless AP sync
uses for `ap_device_type` / `ap_role` / `ap_location`).

### What gets synced

| FortiOS type | → Nautobot | Notes |
|---|---|---|
| `physical` interfaces | `dcim.Interface` type=`1000base-t` | The actual hardware ports (wan1, internal1-7, dmz, modem, etc.) |
| `aggregate` interfaces | `dcim.Interface` type=`lag` | e.g. `fortilink` |
| `hard-switch` interfaces | `dcim.Interface` type=`lag` | the switch parent (e.g. `internal` on FortiWiFi-61E) |
| `switch` interfaces | `dcim.Interface` type=`lag` | soft switches (e.g. `lan`) |
| Interface IPs | `ipam.IPAddress` + auto-created parent `ipam.Prefix` | host IPs assigned via `interface.ip_addresses` |

### What's deliberately skipped

| FortiOS type | Reason |
|---|---|
| `vap-switch` | Already represented via `WirelessNetwork` sync (v2.0+) |
| `vlan` (e.g. `wqtn.X.Y`) | Mostly auto-created quarantine artifacts; defer to v3.1 |
| `tunnel` | VPN-specific; defer to a VPN-focused release |

### Read-only in v3.0 — no push direction

Wrong IP on a FortiGate interface can disconnect the appliance. Push
direction (Nautobot → FortiGate) for device/interface config requires
explicit operator opt-in plus pre-validation; tracked for v3.1+.

### New helper: `fortios_interface_ip_to_cidr`

The FortiOS dotted-mask format `"203.0.113.99 255.255.255.0"` means
different things in different contexts:
- In `firewall.address.subnet`, it's the *network* the AddressObject
  represents → `fortios_subnet_to_cidr()` collapses to `203.0.113.0/24`
- In `system.interface.ip`, it's *this interface's host IP* → the new
  `fortios_interface_ip_to_cidr()` preserves the host: `203.0.113.99/24`

Caught during v3.0 live validation — first sync produced phantom
network addresses, fix produced correct host IPs.

### Live-validated end-to-end

Pull against the dev FortiWiFi-61E synced:
- 1 Device (`fgt-dev`, type=FortiWiFi-61E, role=Firewall, location=Lab)
- 15 Interfaces (10 physical + 4 aggregate/switch/hard-switch + 1 disabled `modem`)
- 4 Interfaces with IPs (`dmz`, `fortilink`, `lan`, `wqt.root`)

Idempotency confirmed: second sync produced `{'create': 0, 'update': 0,
'delete': 0, 'no-change': 16, 'skip': 0}` — clean round-trip.

### Known minor issue (deferred to v3.1)

Device.serial is currently empty — the FortiOS serial-extraction path
tried in `_get_fortios_serial()` doesn't quite work with fortigate-api
2.0.8's response envelope handling. Not blocking; the Device exists
and all its interfaces sync correctly. Operators who want the serial
populated can edit the Device manually until v3.1 fixes the extraction.

### Upgrade from v2026.05.18.10

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

Enable the new Job at **Extensibility → Jobs → "FortiGate → Nautobot
(device + interfaces)"** → click pencil → check Enabled. Then run with
the same ExternalIntegration you use for firewall/wireless sync, plus
the required Nautobot scoping references.

No schema migration. Existing Jobs (firewall pull/push, wireless
pull/push, live status) are unchanged.

## 2026.05.18.10 — v2.9 regression guard: Job.run() lifecycle test (v2.9)

Closes the test gap that allowed v2.9's bug to exist for 8 releases.
No production code changes — every src/ file is byte-identical to v2.9.

### New integration test

`development/scripts/e2e_jobs_lifecycle.py` exercises each of the 4 SSoT
Jobs' ``run()`` override against the dev stack's real Nautobot:

- Instantiates the Job class
- Calls ``run()`` with realistic form kwargs (matches the UI submission)
- Patches the base SSoT ``run()`` to a no-op (skips Celery context
  requirements)
- Asserts that custom form vars (``external_integration``, ``vdom``,
  ``delete_records_missing_from_source``, ``ap_*``) land on the
  instance as the resolved model values, not as the ObjectVar/StringVar
  descriptor objects

Run via `make -C development e2e-jobs-lifecycle`.

### Empirically verified to catch the v2.9 bug

The session record includes a sabotage test: temporarily removed the
``run()`` override from ``FortiGateFirewallDataSource`` (simulating
pre-v2.9 code) and re-ran the test — it correctly reported
"1 of 4 tests FAILED". Restored the code, all 4 pass.

If anyone ever refactors a Job's form-var schema and forgets to update
the corresponding ``run()`` override, this test catches it before any
operator does.

### Why this is its own release

- v2.9 was an urgent hotfix shipped immediately when Kevin reported the
  bug. Adding the regression guard would have delayed the fix.
- v2.10 closes the loop without rushing.
- Same pattern as v2.4 → v2.5: hotfix first, regression guard next.

### Why this isn't a unit test

`tests/conftest.py` stubs out Nautobot and Django entirely for fast unit
testing — but that means the real Job classes can't be exercised there.
The dev container (which the integration test runs in) has the real
environment.

The complementary lifecycle proof is the Playwright UI test from the
session record: it ran the firewall pull Job through the actual Nautobot
web UI on v2.9 and observed `Status: Completed` in 0.43 seconds — i.e.
the full Celery+JobResult+sync path works end-to-end on real hardware.
This integration test handles the unit-test-equivalent contract; the
Playwright session handles the system-integration contract.

### Upgrade from v2026.05.18.9

```bash
pip install --upgrade nautobot-ssot-fortinet
```

No production code changes. No DB migrations. The integration test is
in `development/` and ships in the sdist but only matters if you run
the dev stack.

## 2026.05.18.9 — URGENT HOTFIX: Job.run() instance-attr capture (v2.8)

**Critical bug present in every published version v1.0–v2.8.** Running
any SSoT Fortinet Job through the Nautobot UI crashed immediately with:

```
AttributeError: 'ObjectVar' object has no attribute 'name'
```

Reported by an operator who was the first to actually click "Run Job
Now" through the UI. Every prior verification (e2e scripts, dev seed
data) called the DiffSync adapters directly, bypassing the Job
lifecycle — so the bug never surfaced in our testing.

### Root cause

`nautobot_ssot.contrib.DataSource.run()` and `DataTarget.run()` capture
only their own form vars (``dryrun``, ``memory_profiling``,
``parallel_loading``) into instance attrs. Custom form vars
(``external_integration``, ``vdom``, ``delete_records_missing_from_source``,
``ap_*``) need explicit capture in an overridden ``run()`` method. We
didn't have one, so ``self.external_integration`` resolved to the
class-level ``ObjectVar`` descriptor and crashed at first attribute
access.

### Fix

Added a ``run()`` override to all four broken Jobs:

- ``FortiGateFirewallDataSource``
- ``FortiGateWirelessDataSource``
- ``FortiGateFirewallDataTarget``
- ``FortiGateWirelessDataTarget``

(``FortiGateLiveStatus`` was already correct — it inherits from plain
``Job`` not ``DataSource``, and had its own ``run()``.)

Each override captures the form kwargs as instance attrs, then forwards
``*args, **kwargs`` to ``super().run()``. The base SSoT class continues
to handle ``dryrun`` / ``memory_profiling`` / ``parallel_loading`` as
before.

### Bonus: Updated stale Job description

While in the file: corrected the "Nautobot → FortiGate (firewall)" Job
description from the stale v1.0 string ("Push Nautobot AddressObjects
(ipmask type) to a FortiGate") to reflect what it actually does as of
v2.7 (full CRUD across AddressObject, AddressObjectGroup, ServiceObject,
ServiceObjectGroup, PolicyRule, NATPolicyRule).

### Why our unit + e2e tests didn't catch this

All 202 unit tests pass against v2.8 — they exercise DiffSync models,
utility functions, and adapter behavior with mocked clients. The
``run()`` lifecycle isn't unit-tested because the conftest stubs out
Nautobot/Django entirely (heavy framework imports are too costly for
fast unit tests).

All 8 e2e scripts pass — they construct adapters directly and call
``sync_from()`` themselves, bypassing the Job's ``run()`` path.

**Neither path exercised the Job lifecycle that operators actually
invoke through the Nautobot UI.** A v2.10 follow-up will add an
integration test that actually instantiates and runs a Job through
the real Nautobot lifecycle, in a separate ``tests/integration/`` tree
that runs inside the dev container.

### Live-verified

```python
job = FortiGateFirewallDataSource()
job.run(dryrun=True, memory_profiling=False, parallel_loading=False,
        external_integration=<ExternalIntegration>, vdom='root',
        delete_records_missing_from_source=False)

assert job.external_integration.name == 'fgt-dev'  # ✓
assert job.vdom == 'root'                          # ✓
```

### Upgrade from v2026.05.18.8

```bash
pip install --upgrade nautobot-ssot-fortinet
sudo systemctl restart nautobot nautobot-worker
```

No schema changes. No new Jobs. **If you were hitting the
``AttributeError: 'ObjectVar' object has no attribute 'name'`` error,
this upgrade resolves it. Re-run your Job through the UI.**

## 2026.05.18.8 — Docs screenshots + dev-stack DNS modernization (v2.7)

Documentation polish + dev-stack convenience. No production code
changes — every `src/` file is byte-identical to v2.7.

### Docs

- **Three live UI screenshots** captured via Playwright against the
  dev stack and added to `docs/user/app_getting_started.md`:
  - **Nautobot home dashboard** after sync — shows the synced counts in
    Security / Wireless / IPAM panels (the "what success looks like"
    hero shot)
  - **SSoT dashboard** showing all 4 sync Jobs (2 pull + 2 push) and
    the diagnostic live-status Job, side-by-side
  - **Job runner form** with the External Integration picker, Dryrun
    checkbox, and Vdom field visible — the form operators actually fill
- `docs/user/app_use_cases.md` Use Case 3 (edit-and-push workflow)
  updated to call out v2.6's edit-synth-address propagation explicitly,
  plus the VAP-delete REST limitation from v2.7.
- Getting Started Step 7 now lists every model that supports edit-and-
  push, with the v2.6 / v2.7 capability notes inline.

### Dev stack

- `DOMAIN` in `development/.env` changed from `ssot-fortinet-dev.local`
  to `ssot-fortinet-dev.l.warehack.ing`. The warehack.ing wildcard DNS
  resolves automatically and Caddy gets a real ACME cert via Vultr
  DNS-01 — no more `/etc/hosts` edits required.
- Added `127.0.0.1:8080:8080` port mapping on the dev web container.
  Useful for browser automation (Playwright, headless captures) in
  environments where the wildcard DNS or ACME cert isn't available.
  `ALLOWED_HOSTS` already included localhost so no Nautobot config
  change was needed.
- `CLAUDE.md` updated to reflect both changes.

### Upgrade from v2026.05.18.7

```bash
pip install --upgrade nautobot-ssot-fortinet
```

No DB migration, no Job changes, no config changes. The docs site
(RTD) will auto-rebuild from the new tag.

If you're running the dev stack yourself: `make -C development up` will
recreate the web container with the new label + port mapping.

## 2026.05.18.7 — Remaining CRUD live-validated; DELETE status checking (v2.6)

Closes the "every push CRUD path has a focused live e2e test" gap from
the v2.5 audit. Five new e2e scripts cover AddressObject,
AddressObjectGroup, ServiceObject, ServiceObjectGroup, and
WirelessNetwork (VAP). All seven DELETE callsites now check FortiOS
HTTP status — silent delete failures (the symptom that bit us in v2.4
for create/update) can no longer mask issues.

### New e2e scripts in `development/scripts/`

| Script | Validates | Makefile |
|---|---|---|
| `e2e_push_address.py` | AddressObject CRUD | `make e2e-push-address` |
| `e2e_push_addrgrp.py` | AddressObjectGroup CRUD (M2M change) | `make e2e-push-addrgrp` |
| `e2e_push_service.py` | ServiceObject CRUD | `make e2e-push-service` |
| `e2e_push_svcgrp.py` | ServiceObjectGroup CRUD (M2M change) | `make e2e-push-svcgrp` |
| `e2e_push_vap.py` | WirelessNetwork CREATE + UPDATE | `make e2e-push-vap` |

Plus `make e2e-push-all` runs every push e2e script in sequence.

### `check_fortios_response()` now wraps DELETE callsites

v2.4 wrapped create/update. v2.7 closes the gap for the remaining 7
delete callsites (`firewall.address`, `firewall.addrgrp`,
`firewall_service.custom`, `firewall_service.group`, `firewall.policy`,
`firewall.vip`, `wireless_controller.vap`). If FortiOS rejects a
delete, we now raise `FortiOSAPIError` with the FortiOS error code
and cli_error instead of silently logging "Deleted successfully."

### FortiOS quirks surfaced during validation

Both worth documenting in operator-facing docs (planned v2.8):

- **VAP DELETE via REST is fundamentally broken in FortiOS.** Creating
  a VAP auto-creates a dependent quarantine interface
  (`wqtn.<vlanid>.<truncated-vap-name>`). When you try to delete the
  VAP, FortiOS returns error -23: "Vap quarantine interface ... is in
  use." When you try to delete the quarantine interface first, FortiOS
  returns -23: "The entry is used by other 1 entries." Circular
  dependency, no REST workaround. **Operators must use the FortiGate
  web UI's VAP delete wizard** which handles the dependency teardown.
  `e2e_push_vap.py`'s DELETE phase is documented and skipped.

- **The `internal` interface on FortiWiFi/FortiGate-D devices is a
  switch-parent**, not a usable policy endpoint. Use `internal1`-
  `internal7` (or define a zone). Hit during e2e_push_policy work in
  v2.5 — now documented.

### Tests

- 202 unit tests still passing (no test changes needed; the new e2e
  scripts are integration tests, not unit tests).

### Live-validated end-to-end against FortiWiFi-61E

| Path | CREATE | UPDATE | DELETE |
|---|---|---|---|
| AddressObject | ✓ | ✓ (prefix change) | ✓ |
| AddressObjectGroup | ✓ | ✓ (M2M add) | ✓ |
| ServiceObject | ✓ | ✓ (description) | ✓ |
| ServiceObjectGroup | ✓ | ✓ (M2M add) | ✓ |
| WirelessNetwork (VAP) | ✓ | ✓ (enabled toggle) | ⚠ FortiOS REST limitation |
| PolicyRule | ✓ (v2.4) | ✓ (v2.4) | ✓ (v2.4) |
| NATPolicyRule | ✓ (v2.4/2.5) | ✓ + value-change (v2.4/2.6) | ✓ (v2.4) |
| RadioProfile / wtp-profile | ✓ (v2.4) | n/a | n/a |

### Upgrade from v2026.05.18.6

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

No schema changes. No new Jobs. The behavior change is: previously-
silent delete failures will now raise `FortiOSAPIError`. If you depend
on those silent failures somehow (you shouldn't), you'd see surfaced
errors — investigate the FortiOS cli_error in the exception message.

## 2026.05.18.6 — NAT update propagates from address-value-change (v2.5)

Closes the v2.5 deferred design question. Editing the IP of an existing
`vip_*_mapped` or `vip_*_ext` AddressObject in Nautobot now propagates
to the FortiGate's VIP record on push — operator workflow finally
matches the obvious mental model.

### What changed

- **New `resolved_extip` + `resolved_mappedip` DiffSync attrs** on
  `NATPolicyRule`. They carry the ACTUAL IP values that
  `original_destination_addresses` / `translated_destination_addresses`
  resolve to. Populated by both the FortiGate pull adapter (uses the
  values directly from FortiOS extip/mappedip) and the Nautobot adapter
  (resolves the first AddressObject in each M2M to its IP via
  `_orm_address_value()`).
- **Push side acts on `resolved_*` diffs.** `FortiGateNATPolicyRule.update()`
  now POSTs `mappedip` / `extip` when the resolved-value fingerprint
  changes (the v2.6 path) — in addition to the pre-v2.6 M2M-name-change
  path which still works for backwards-compat.

### Why this matters

Pre-v2.6 operator workflow that didn't work:
```
1. UI: open vip_X_mapped AddressObject, change IP 10.0.0.50 → 10.0.0.99
2. Push.
3. FortiGate's VIP still shows mappedip 10.0.0.50.   ❌ silent failure
```

The rule's `translated_destination_addresses` M2M still pointed to the
same record by name, so DiffSync saw no rule-level diff, so
`NATPolicyRule.update()` never fired. Operators had to replace the
AddressObject reference instead — not a natural workflow.

Post-v2.6 the same operator workflow works:
```
1. UI: open vip_X_mapped AddressObject, change IP 10.0.0.50 → 10.0.0.99
2. Push.
3. FortiGate's VIP now shows mappedip 10.0.0.99.    ✓ live-verified on FWF-61E
```

### Live-validated

Same `e2e_push_nat.py` script that was used to surface the v2.5 issue,
now updated to use the edit-value workflow (`mapped_addr.ip_address =
new_ip; mapped_addr.save()`). Passes end-to-end against FortiWiFi-61E
(FortiOS 7.0.14).

### Tests

- **202 unit tests** (was 201 in v2.5). +1 covering
  `resolved_extip` / `resolved_mappedip` fingerprint population on
  the FortiGate pull adapter.

### Why this isn't backwards-compatible breakage

The new attrs are **additive** — existing M2M-name-change diffs still
fire `update()` as before. The only behavior change is: previously-silent
value-changes now produce a diff. Anyone whose workflow relied on
"editing the address value doesn't trigger a push update" was operating
against intent, not by design.

### Upgrade from v2026.05.18.5

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

No new Jobs. No schema changes. Re-running the pull Job once after
upgrade is recommended (refreshes the new `resolved_*` attrs into
Nautobot's view).

## 2026.05.18.5 — Policy + NAT push live-validated; round-trip stability (v2.4)

Direct follow-up to the v2.4 hotfix retrospective. Policy and NAT push
were claimed to work in v2.0/v2.1 but never actually exercised against
a real FortiGate (the broken `.get(uid=...)` verification pattern
produced false positives). This release adds focused end-to-end live
test scripts AND fixes two round-trip stability bugs surfaced by them.

### Live-validated push paths

Two new e2e scripts in `development/scripts/` (mountable into the dev
stack via `make e2e-push-policy` / `make e2e-push-nat`):

- **`e2e_push_policy.py`** — full PolicyRule CRUD against fgt-dev:
  inject Nautobot rule → push CREATE → verify on device → toggle log
  field → push UPDATE → verify → delete from Nautobot → push DELETE →
  verify gone. Uses `policyid=9999` (well outside operator range) and
  references existing FortiGate addresses for isolation.
- **`e2e_push_nat.py`** — NATPolicyRule (VIP) CRUD with the
  synthesized `vip_*_ext`/`vip_*_mapped` AddressObject round-trip.
  Uses RFC 5737 documentation IPs and replaces the address pointer
  (not the IP value) for the UPDATE step.

Both tests passed against a live FortiWiFi-61E running FortiOS 7.0.14.

### Round-trip stability fixes

- **`/32` ipmask addresses normalize to `ipaddress` on pull.** FortiOS
  has no separate "host" address type — IPv4 host IPs are always stored
  as `type=ipmask, subnet='IP 255.255.255.255'`. Pre-v2.5 we classified
  this as `ipmask` with value `'IP/32'`, but push code maps DiffSync
  `ipaddress` back to FortiOS `ipmask /32`. Result: round-trip asymmetry
  → phantom diffs on every push. Now both directions converge.
- **`strip_pull_annotations()` helper** strips machine-generated
  `[srcintf=...]`, `[extintf=...]`, `[portforward ...]` markers from
  the FortiOS comment BEFORE re-adding them on subsequent pulls.
  Pre-v2.5 the annotation doubled on every round-trip cycle
  (`[extintf=wan1] [extintf=wan1]` → triple → infinite). Operator-added
  brackets (`[CHANGE-1234]`) are preserved.

### Known minor cosmetic — clears with first pull after upgrade

If you upgrade with existing Nautobot AddressObject records that were
loaded under the pre-v2.5 ipmask /32 classification, you'll see a
one-time phantom diff for those records (now `ipaddress` from the
FortiGate side, still `ipmask` from the ORM). **Run the pull Job once
after upgrading** — it'll rewrite those ORM records under the new
classification. After that, push diffs are stable.

### Findings deferred to v2.6+ design

- **NAT update via address-value-change doesn't propagate.** If an
  operator edits the IP of an existing `vip_*_mapped` AddressObject,
  the rule's `translated_destination_addresses` M2M still references
  the same record by name → no rule-level diff → no `vip.update()`.
  Architecturally clean workaround today: point the rule at a different
  AddressObject. Open design question: should the rule's diff fingerprint
  the resolved IP *values* too, so a referenced address changing triggers
  a rule diff?

### Tests

- **201 unit tests** (was 193 in v2.4)
- +8 tests for `strip_pull_annotations` (each annotation type + operator
  bracket preservation + idempotency + empty-string + passthrough)
- 1 corrected test (`test_host_via_32_mask_becomes_slash_32` →
  `test_host_via_32_mask_becomes_ipaddress`) — was asserting the
  round-trip-breaking behavior we just fixed.

### Upgrade from v2026.05.18.4

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
# Recommended: run the pull Job once to migrate any pre-v2.5 /32
# AddressObject records to the new ipaddress classification.
```

No new Jobs. No schema changes.

## 2026.05.18.4 — Push direction hotfix: actually-works edition (v2.3)

Hotfix for v2.2 (2026.05.18.3) and **multiple latent bugs from v2.0+**
that were masked by mock-based unit tests and a buggy verification
pattern. Live validation against a real FortiWiFi-61E surfaced all of
them. **If you ran any push Job in v2.0–v2.2 and saw "Created/Updated
successfully" logs, your sync most likely did nothing on the FortiGate**
— this release fixes that.

### Critical fixes

- **`Connector.update(uid=..., data=...)` was broken across 10 callsites
  since v2.0.** fortigate-api's `Connector.update(self, data)` takes
  only `data` — the uid lives inside the data dict (`data["name"]` or
  `data["policyid"]`). Pre-v2.3 every push *update* path raised
  `TypeError: Connector.update() got an unexpected keyword argument 'uid'`
  on the live device. Mock-based unit tests didn't catch it because
  `MagicMock()` accepts any kwargs silently. Fixed across:
  `firewall.address`, `firewall.addrgrp`, `firewall_service.custom`,
  `firewall_service.group`, `firewall.policy`, `firewall.vip`,
  `wireless_controller.vap`, `wireless_controller.wtp_profile` (×2 sites).
- **`_radio_payload()` built the wrong channel format.** Sent
  `channel: ["1", "6", "11"]` (flat list of strings); FortiOS requires
  `channel: [{"chan": "1"}, {"chan": "6"}, {"chan": "11"}]` (list of
  objects). Empirically probed against FortiOS v7.0.14. Flat lists
  returned http=500 / error=-1 silently.
- **`wtp-profile.create` comment with parentheses rejected as XSS** by
  FortiOS (error -173: "The string contains XSS vulnerability characters").
  Switched default comment to use `[N radios]` brackets instead of
  `(N radios)` parens.
- **No HTTP status checking on create/update responses.** All FortiOS
  rejections (HTTP 500 + error code) were silently dropped. Added
  `check_fortios_response()` helper that raises `FortiOSAPIError` with
  the FortiOS error code, `cli_error` text, and a label identifying
  which call failed. **All 17 create/update callsites now check status.**

### Verification-script bug class (development/, not shipped)

- `Connector.get(uid=...)` doesn't filter by uid — it fetches everything
  and returns the full list. The correct call is `.get(name='x')` (or
  whatever the endpoint's `uid` class attribute is). Pre-v2.3 our
  verification scripts did `found = api.get(uid=NAME); rec = found[0]`
  — silently picking an unrelated record as the "verified" object.
  **This is how every "live validated against FWF-61E" claim across
  v1.0–v2.2 became a false positive for anything beyond pull/load shape.**
  Fixed in `development/scripts/e2e_push_validate.py` and
  `e2e_push_wtp_profile.py`.

### v2.2 wtp-profile create — NOW actually works

The v2.2 sibling-aggregation create path was non-functional in
2026.05.18.3 (silent HTTP 500 due to channel-format + comment-XSS
issues). After the v2.3 fixes, a focused live test against FWF-61E
confirms end-to-end:

```
Nautobot RadioProfile(profile=guest, radio_index=1, 2.4GHz, channels=[1,6,11])
Nautobot RadioProfile(profile=guest, radio_index=2, 5GHz,   channels=[36,40,44,48])
       ↓ Nautobot → FortiGate (wireless) Job
FortiGate wtp-profile 'guest':
  radio-1 band='802.11n,g-only'  channels populated
  radio-2 band='802.11ac'        channels populated
       ↓ Hardware-appropriate band normalization by FortiOS:
       (FWF-61E is 802.11ac, so 802.11ax-5G normalized to 802.11ac)
```

### Tests

- **193 unit tests** (was 188 in v2.2). +4 for `check_fortios_response`
  behavior, +1 regression guard for `Connector.update()` signature
  using `MagicMock(spec=Connector)` — the spec'd mock fails at unit-test
  time if anyone reintroduces `uid=` kwarg.
- 1 corrected test (`test_create_does_partial_update_when_target_sibling_exists`)
  now asserts `data["name"]` instead of `uid=`.
- All ruff lint + format clean.

### Recommendation if upgrading from v1.0–v2.2

If your push Jobs ever showed "Updated successfully" but you observed
state on the FortiGate that didn't reflect your Nautobot changes,
the cause was the `uid=` bug. After upgrading to v2.3:

1. Run the relevant pull Job to refresh Nautobot's view of the FortiGate
2. Compare with what you expected — anything you thought you had pushed
   but didn't is now a real diff
3. Re-run the push Job; the writes will now actually land

### Upgrade from v2026.05.18.3

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

No Job count change (still 5). No DiffSync attr changes.

## 2026.05.18.3 — wtp-profile CREATE via sibling aggregation (v2.2)

> **NOTE (added 2026-05-18, post-release):** the wtp-profile create code
> path shipped in this release was **non-functional** against real
> FortiOS due to bugs documented in v2.3 (2026.05.18.4). The code path
> exists and unit tests pass, but live POSTs returned HTTP 500 silently.
> Upgrade to v2.3+ for actually-working wtp-profile create.


Fourth release today. Closes the last remaining CREATE gap from v2.1:
**RadioProfile push can now create the parent wtp-profile from scratch**
when it doesn't yet exist on the FortiGate. Push is now full-CRUD across
every model.

### Added

- **`FortiGateRadioProfile.create()` aggregates siblings.** When DiffSync
  invokes `create()` for a new RadioProfile and the parent wtp-profile
  doesn't exist on the FortiGate, the model now reaches into the source
  adapter, collects ALL RadioProfiles that share the same
  `original_profile_name`, and POSTs one combined wtp-profile payload
  with all `radio-N` subfields populated at once. Subsequent sibling
  `create()` calls notice the wtp-profile is now present and become
  per-radio `update()` calls — the typical FortiOS partial-update path.
- **Source adapter hand-off in push Jobs.** Both push Jobs
  (`FortiGateFirewallDataTarget`, `FortiGateWirelessDataTarget`) now
  stash `self.target_adapter.source_adapter = self.source_adapter`
  right before `execute_sync()`. This is what makes sibling aggregation
  observable from inside model `create()` methods. The firewall side
  doesn't need it today, but the symmetry keeps the pattern discoverable.
- **5 new unit tests** in `tests/test_models_target_wireless.py`
  covering all three branches of `FortiGateRadioProfile.create()`:
  missing `original_profile_name`, target sibling exists (partial update
  path), source aggregation with 2+ radios, source aggregation with 1
  radio, missing source adapter (warn + skip).

### Design notes

- **Default `platform-mode: "FortiAP-tunnel-mode"`.** The wtp-profile's
  `platform-mode` field doesn't have a per-radio equivalent in Nautobot,
  so we default to the most common managed-FortiAP value. Operators
  running mesh / bridge / local-flex modes override on the FortiGate UI
  after first sync — once set there, the value sticks (we don't push it
  on per-radio updates).
- **Why aggregation in `create()` and not in the adapter.** DiffSync
  emits per-record `create()` calls. Pre-aggregating at the adapter
  level would have meant doing FortiOS writes outside the diff machinery
  — losing dry-run support, diff summaries, and progress logs. Doing it
  in `create()` keeps everything inside the DiffSync orchestration loop.

### Tests

- **188 unit tests** total (was 183 in v2.1). +5 for sibling aggregation.
- All ruff lint + format clean.

### Workflow now unlocked

Operators can create a brand-new wireless profile entirely in Nautobot:

```
   Nautobot UI: Create RadioProfile("guest", radio_index=1, freq=2.4GHz, ...)
                Create RadioProfile("guest", radio_index=2, freq=5GHz, ...)
        ↓
   Run "Nautobot → FortiGate (wireless)" Job (dry-run first!)
        ↓
   FortiGate has new wtp-profile "guest" with radio-1 + radio-2 populated.
```

Pre-v2.2 workaround: create the wtp-profile shell on the FortiGate UI
first, then push the RadioProfiles. No longer needed.

### Upgrade from v2.1

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

No new Jobs (still 5). No schema changes. No new DiffSync attrs. The
RadioProfile push path is simply more capable now.

## 2026.05.18.2 — PolicyRule + NATPolicyRule CREATE (v2.1)

Third release today. Removes the v2.0 deferral of CREATE for policies
and NAT VIPs. All push directions are now full-CRUD except wtp-profile
(which still needs multi-radio aggregation).

### Added

- **`PolicyRule.source_interfaces` + `destination_interfaces`** as
  first-class structured DiffSync attrs. The pull side populates them
  from FortiOS `srcintf` / `dstintf`; the Nautobot adapter parses them
  back from the description's `[srcintf=lan dstintf=wan1]` annotation
  on load. The description doubles as human-readable annotation AND
  structured storage.
- **`NATPolicyRule.external_interface`** as a first-class attr; same
  pattern (parsed from `[extintf=wan1]`).
- **`PolicyRule` CREATE** on push — uses the new interface attrs to fill
  in FortiOS's required `srcintf`/`dstintf`. Falls back to `["any"]`
  when an attr is empty (FortiOS accepts that as wildcard).
- **`NATPolicyRule` CREATE** on push via full VIP reconstruction —
  resolves the synthesized `vip_*_ext` / `vip_*_mapped` AddressObjects
  back to their IP values for `extip` / `mappedip[].range`, populates
  `extintf` from the structured attr, and optionally adds port-forward
  from the translated services.
- New `parse_intf_annotation()` helper in `utils.fortios` with 9 unit
  tests covering the round-trip.

### Workflow unlocked

Operators can now author firewall policies and NAT VIPs **entirely in
Nautobot** and push them to FortiGate from scratch:

```
   Nautobot UI: Create PolicyRule(source=A, dest=B, action=allow,
                                  source_interfaces=[lan], ...)
        ↓
   Run "Nautobot → FortiGate (firewall)" Job (dry-run first!)
        ↓
   FortiGate has the new policy. Verify on FortiGate web UI.
```

Pre-v2.1 the workaround was "create the policy on the FortiGate UI
first, then pull"; that's no longer needed.

### Still deferred to v2.2

- **wtp-profile create from a single RadioProfile** — requires
  multi-radio + platform-mode aggregation that isn't expressible at
  the per-radio DiffSync level.

## 2026.05.18.1 — Wireless push + policy/NAT push (UPDATE/DELETE)

Same-day follow-up to v1.0 — extends the push direction across wireless,
policy, and NAT. **5 Jobs registered now**, with the new
**"Nautobot → FortiGate (wireless)"** appearing alongside the existing four.

### Added

- **Wireless push Job: `Nautobot → FortiGate (wireless)`** — pushes
  Nautobot wireless config to a FortiGate.
  - `WirelessNetwork` (VAP) — full create/update/delete via
    `cmdb/wireless-controller/vap`. SSID, security mode, broadcast,
    enabled, description all round-trip.
  - `RadioProfile` — **update-only** via partial wtp-profile updates
    (`wtp-profile.radio-N` payload). Parent wtp-profile must exist on
    the device; create of a single radio isn't well-defined.
- **Policy push** in the existing firewall push Job — `PolicyRule`
  update + delete. Operators can edit a policy's allowed
  addresses/services/action/log in Nautobot's UI and push the change
  back to the FortiGate. The `policyid` is parsed from the mangled name
  suffix (`<host>__<vdom>__rule_<N>`).
- **NAT push** — `NATPolicyRule` update + delete via FortiOS VIP
  partial-update + delete. The push resolves the synthesized
  `vip_*_mapped` AddressObject back to its IP value for the
  `mappedip[].range` payload.

### Mapping additions

- Inverse `NAUTOBOT_AUTH_TO_FORTIOS_SECURITY` table — Nautobot
  WirelessNetworkAuthenticationChoices → FortiOS `vap.security` value.
  When multiple FortiOS values map to one Nautobot choice (e.g.
  `wpa-personal` and `wpa2-only-personal` both → `WPA2 Personal`), we
  pick the most-modern form on push.
- Inverse `NAUTOBOT_ACTION_TO_FORTIOS` table — handles the asymmetry
  where firewall-models distinguishes `drop` from `deny` but FortiOS
  rolls them together.

### Deferred to v2.1

- **PolicyRule create from scratch** — requires `srcintf`/`dstintf` which
  aren't yet stored as structured DiffSync attrs (they live in the
  rule's description for diagnostic purposes only). Operators must
  create the policy on the FortiGate UI first, then pull into Nautobot.
- **NATPolicyRule (VIP) create from scratch** — same `extintf` issue.
- **wtp-profile create from a single RadioProfile** — needs the full
  multi-radio + platform-mode context we don't have at the RadioProfile
  level.

## 2026.05.18 — v1.0

First release. Bidirectional Nautobot ↔ FortiGate sync, live-validated
against a FortiWiFi-61E.

### Added

- **Pull Job: FortiGate → Nautobot (firewall)** — syncs addresses, address
  groups, services, service groups, policies + rules, NAT (VIPs) into
  `nautobot-firewall-models`.
- **Pull Job: FortiGate → Nautobot (wireless)** — syncs WirelessNetworks
  (SSIDs), RadioProfiles (radios fanned out per profile), optionally
  FortiAP Devices.
- **Live status Job: FortiGate Live Status** — real-time observability,
  joins `monitor/wifi/client` + `monitor/system/dhcp` +
  `monitor/network/arp` by MAC, attaches JSON snapshot to Job result.
- **Push Job: Nautobot → FortiGate (firewall)** — pushes address objects
  (4 types), address groups, service objects, service groups back to the
  FortiGate REST API.
- Credential support: API token (FortiOS 5.6+) preferred, username +
  password fallback.
- Synthetic AddressObjects + ServiceObjects for FortiOS VIPs (DNAT) —
  VIPs inline their IPs/ports, so the integration manufactures the
  required Nautobot referents on the fly.
- Live e2e harnesses (`make e2e-live-firewall`, `make e2e-live-wireless`,
  `make e2e-push-validate`) that exercise the full sync against a real
  FortiGate with idempotency assertions.
- Fixture-based e2e harnesses (`make e2e-firewall`, `make e2e-wireless`)
  that use mocked clients + real Nautobot ORM, for CI.
- 174 unit tests covering all pure-function helpers + adapter behaviors.

### FortiOS quirks handled

- `interface-subnet` address type → treated as `ipmask` (resolved CIDR)
- Space-separated multi-port (KERBEROS `"88 464"`) → normalized to comma
  for firewall-models' validator (which has a buggy error template)
- `ICMP6` → mapped to `IPv6-ICMP` (IANA name used by firewall-models)
- `'513:512-1023'` src-port qualifier (RLOGIN/RSH) → source-port stripped
- `protocol: "ALL"` pseudo-protocol (webproxy) → skipped
- `protocol: "IP"` + `protocol-number` → mapped to named IANA protocol
  (e.g. 89 → `OSPFIGP`)
- WTP-profile multi-mode-per-VAP → most-common platform-mode wins
- FortiOS WEP / captive-portal security → mapped to `Open` with
  annotation in description

### Architecture decisions

- DiffSync vendor-neutral models in `diffsync/models/{firewall,wireless}.py`;
  per-target CRUD subclasses in `diffsync/models/{nautobot_*,fortigate_*}.py`
- Name mangling `<hostname>__<vdom>__<original>` for cross-device
  uniqueness, except `ServiceObject` (composite NK)
- Sort all M2M member lists at adapter-load time for stable diffs
  (Django M2M is unordered)
- Additive-only sync by default; destructive deletes opt-in per Job
- `with build_client(ext) as fgt:` context manager for single-session auth

### Not yet in scope

- Policy/NAT push (pull works; push is a future iteration due to M2M
  complexity)
- Source NAT (FortiOS `ippool`) — pull only handles DNAT (VIPs)
- IPv6 addresses (`firewall/address6`) — IPv4 only
- Multi-VDOM aware Nautobot UI

### Verified compatibility

- Nautobot 3.1.2 + nautobot-ssot 4.2.2 + nautobot-firewall-models 3.0.0
- fortigate-api 2.0.8 against FortiOS 7.x (FortiWiFi-61E)
- Python 3.10, 3.11, 3.12, 3.13
