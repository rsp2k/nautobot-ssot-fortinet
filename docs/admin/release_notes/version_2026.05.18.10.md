# v2026.05.18.10 — v2.9 regression guard: Job.run() lifecycle test (v2.9)

Closes the test gap that allowed v2.9's bug to exist in v1.0–v2.8.
**No production code changes** — every file under `src/` is byte-identical
to v2.9.

## The lesson v2.9 taught

v1.0–v2.8 had 202 unit tests and 8 e2e scripts, all passing. None of
them caught the bug Kevin reported. The reason: **neither layer
exercised the Job's `run()` lifecycle**:

- **Unit tests** (`tests/`) use a conftest that stubs out Nautobot and
  Django entirely. Fast to run, but can't instantiate real Job classes.
- **E2E scripts** (`development/scripts/e2e_push_*.py`) call DiffSync
  adapters directly via `sync_from()`. Bypasses the Job class entirely.

The first real test of `Job.run()` was Kevin clicking "Run Job Now" in
the UI. That's the wrong place for the first test.

## What this release adds

`development/scripts/e2e_jobs_lifecycle.py` — runs inside the dev
container against the real Nautobot environment. For each of the 4 SSoT
Jobs:

1. Instantiates the Job class
2. Calls `run()` with realistic form kwargs (matches what the UI
   submits)
3. Patches the base SSoT class's `run()` to a no-op (skips Celery
   context requirements — they're not in the v2.9 contract scope)
4. Asserts custom form vars land on the instance as resolved model
   values, not ObjectVar/StringVar descriptors

Run via:

```bash
make -C development e2e-jobs-lifecycle
```

Sample output:

```
[test] FortiGateFirewallDataSource (pull) — the v2.9 reported case
  ✓ PASS — all 3 instance attrs captured correctly
[test] FortiGateWirelessDataSource (pull) — has optional ap_* form vars
  ✓ PASS — all 6 instance attrs captured correctly
[test] FortiGateFirewallDataTarget (push) — DataTarget lifecycle
  ✓ PASS — all 3 instance attrs captured correctly
[test] FortiGateWirelessDataTarget (push) — DataTarget lifecycle
  ✓ PASS — all 3 instance attrs captured correctly
✓ All 4 attribute-capture tests PASSED
```

## Verified to actually catch v2.9's bug

Before committing, I sabotaged the fix: temporarily removed the `run()`
override from `FortiGateFirewallDataSource` (simulating pre-v2.9 code)
and re-ran the test. It correctly reported "1 of 4 tests FAILED" with
the regression detail. Restored the code, all 4 pass.

**If anyone ever refactors a Job's form-var schema and forgets to
update the corresponding `run()` override, this test catches it before
any operator does.**

## Why this is its own release

v2.9 was an urgent hotfix shipped immediately when Kevin reported the
bug. Adding the regression guard would have delayed the fix.

v2.10 closes the loop without rushing. Same pattern as v2.4 → v2.5:
hotfix first, regression guard next.

## Two complementary lifecycle proofs now exist

| Proof | Layer | What it asserts |
|---|---|---|
| `e2e_jobs_lifecycle.py` (v2.10) | Unit-test equivalent for Job classes | Custom form vars are captured as instance attrs |
| Playwright UI session (v2.9 release notes) | System integration | Real Job runs through real Celery + JobResult + sync, completes in 0.43s |

Both pass against v2.9+. The first runs in the dev container in
seconds; the second requires the dev stack + a live FortiGate.

## Upgrade from v2026.05.18.9

```bash
pip install --upgrade nautobot-ssot-fortinet
```

No production code changes. No DB migrations. No new Jobs. The
integration test ships in the sdist but only matters when you run the
dev stack with `make e2e-jobs-lifecycle`.

## Reflection

This is the second time today the discipline showed: **identify which
live path you're testing**. v2.3 surfaced that adapter-level live tests
weren't catching push-direction bugs (we'd been testing pull). v2.9
surfaced that Job-level tests weren't covered at all. v2.10 closes the
last layer.

The session's release sequence — v2.3 → v2.4 → v2.5 → v2.6 → v2.7 →
v2.8 → v2.9 → v2.10 — traces a path from "claims things work" to
"measurably proves things work, at every operator-facing entry point."
The codebase is the same shape it was this morning. The confidence
floor is fundamentally different.
