# v2026.05.18.6 — NAT update propagates from address-value-change (v2.5)

Closes the design question deferred from v2.5. Editing the IP value of
an existing `vip_*_mapped` or `vip_*_ext` `AddressObject` in Nautobot
now propagates to the FortiGate's VIP on push — no operator workflow
constraint required.

## The story

v2.5 surfaced a gap: the *natural* operator workflow ("click the VIP's
synth address in the UI, change the IP, push") didn't work. The rule's
`translated_destination_addresses` M2M still pointed to the same
AddressObject record by name → DiffSync saw no rule-level diff →
`NATPolicyRule.update()` never fired → FortiOS's VIP record (which
stores `mappedip` as a literal value, not a reference) stayed stale.

Workaround in v2.5: replace the AddressObject *reference* on the rule.
That works architecturally but is surprising to UI-driven operators.

v2.6 fixes this at the right layer: make the resolved IP value part of
the rule's DiffSync fingerprint.

## How

Two new fields on `NATPolicyRule`:

| Field | What it carries |
|---|---|
| `resolved_extip` | The actual IP value the first `original_destination_addresses` resolves to |
| `resolved_mappedip` | The actual IP value the first `translated_destination_addresses` resolves to |

Populated on both sides:
- **FortiGate pull adapter** — uses the VIP's `extip` / `mappedip` values directly from FortiOS
- **Nautobot adapter** — resolves the AddressObject via the existing `_orm_address_value()` helper

Since these values are part of `_attributes`, any change at the value
level produces a rule-level diff → `NATPolicyRule.update()` fires →
`vip.update()` POSTs the new mappedip/extip.

## Before vs after

```
# Pre-v2.6 (silent failure)
Nautobot UI: edit vip_X_mapped.ip_address 10.0.0.50 → 10.0.0.99
Push:
   AddressObject.update() fires → FortiOS address record updated
   NATPolicyRule diff: empty                    # ← bug
   NATPolicyRule.update() doesn't fire
FortiGate VIP mappedip: still 10.0.0.50         # ❌

# v2.6 (works as expected)
Nautobot UI: edit vip_X_mapped.ip_address 10.0.0.50 → 10.0.0.99
Push:
   AddressObject.update() fires → FortiOS address record updated
   NATPolicyRule diff: { resolved_mappedip: 10.0.0.50 → 10.0.0.99 }
   NATPolicyRule.update() fires → POST mappedip=[{range: 10.0.0.99}]
FortiGate VIP mappedip: 10.0.0.99               # ✓ live-verified
```

## Live-validated

The same `e2e_push_nat.py` script that surfaced the v2.5 limitation,
now updated to edit the IP value of the existing AddressObject (no
pointer-replace gymnastics). Passes end-to-end against FortiWiFi-61E
on FortiOS 7.0.14:

```
[3/5] Update mappedip 10.0.0.50 → 10.0.0.99 via EDIT-VALUE on existing AddressObject...
    mappedip ranges: ['10.0.0.99']
    ✓ mappedip updated to '10.0.0.99'
```

Run yourself: `make -C development e2e-push-nat`.

## Backwards compatibility

The new attrs are **additive** — existing M2M-name-change diffs still
fire `update()` as before, so the pointer-replace workflow keeps
working. The only behavior change is: previously-silent value-changes
now produce a diff. If you were depending on "editing the IP doesn't
propagate to FortiOS," that was a bug, not a feature.

## Tests

- **202 unit tests** (was 201 in v2.5)
- +1 covering `resolved_extip` / `resolved_mappedip` fingerprint
  population from the FortiGate pull adapter (single-IP extip and
  range-form mappedip both verified)

## Upgrade from v2026.05.18.5

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
# Recommended: run the pull Job once to populate the new resolved_*
# attrs on existing NATPolicyRule records.
```

No new Jobs (still 5). No schema changes. The new DiffSync attrs are
purely model-level — no Nautobot DB migration.

## Reflection

This was the cleanest design path because it addressed the diff
correctness gap at the *diff layer* — not by introducing cross-model
coupling in `update()` handlers, not by inventing a workflow constraint
for operators, not by special-casing synth addresses anywhere.

The pattern generalizes: any DiffSync model whose attrs are
"references to other records by name" should consider also fingerprinting
the *resolved values* of those references when the target system stores
them literally. We've now done it for NAT; if other models develop
similar symptoms, the playbook is established.
