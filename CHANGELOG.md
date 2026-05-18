# Changelog

This project uses [CalVer](https://calver.org/) — versions are `YYYY.MM.DD`
representing the date of release. Same-day fixes use `YYYY.MM.DD.N`.

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
