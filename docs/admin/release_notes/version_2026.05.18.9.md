# v2026.05.18.9 — URGENT HOTFIX: Job.run() instance-attr capture (v2.8)

**Critical bug present in every published version v1.0–v2.8.** Running
any SSoT Fortinet Job through the Nautobot UI crashed immediately with:

```
AttributeError: 'ObjectVar' object has no attribute 'name'
```

## If you're hitting this error

Upgrade immediately:

```bash
pip install --upgrade nautobot-ssot-fortinet  # → 2026.5.18.9
sudo systemctl restart nautobot nautobot-worker
```

Re-run the Job through the UI. The error is resolved.

## What was broken

`nautobot_ssot.contrib.DataSource.run()` and `DataTarget.run()` capture
only their own form vars (`dryrun`, `memory_profiling`,
`parallel_loading`) into instance attrs. Custom form vars like
`external_integration`, `vdom`, `delete_records_missing_from_source`
need explicit capture in an overridden `run()` method.

Without that override, `self.external_integration` resolved to the
class-level `ObjectVar` descriptor — and the first line of
`load_source_adapter()` that calls `.name` on it crashed.

## What's fixed

Added a `run()` override to all four broken Jobs:

- `FortiGateFirewallDataSource`
- `FortiGateWirelessDataSource`
- `FortiGateFirewallDataTarget`
- `FortiGateWirelessDataTarget`

(`FortiGateLiveStatus` was already correct — it inherits from plain
`Job` not `DataSource`, and already had its own `run()`.)

The pattern:

```python
def run(self, *args, **kwargs):
    self.external_integration = kwargs["external_integration"]
    self.vdom = kwargs["vdom"]
    self.delete_records_missing_from_source = kwargs["delete_records_missing_from_source"]
    super().run(*args, **kwargs)
```

## Why this bug survived 8 releases

- **All 202 unit tests pass against v2.8.** They exercise DiffSync
  models, utility functions, and adapter behavior with mocked clients.
  The `run()` lifecycle isn't unit-tested because the conftest stubs out
  Nautobot/Django entirely (framework imports are too heavy for fast
  unit tests).
- **All 8 e2e scripts pass.** They construct adapters directly and call
  `sync_from()` themselves, bypassing the Job's `run()` path.
- **The first real-world test was the first operator who clicked "Run
  Job Now"** through the Nautobot UI. That path is what our tests
  weren't exercising.

A v2.10 follow-up will add an integration test that actually runs a
Job through the real Nautobot lifecycle, in a `tests/integration/`
tree that runs inside the dev container.

## Bonus: stale Job description fix

While in the file: corrected the "Nautobot → FortiGate (firewall)" Job
description from the stale v1.0 string ("Push Nautobot AddressObjects
(ipmask type) to a FortiGate") to reflect actual v2.7 capabilities
(full CRUD across AddressObject, AddressObjectGroup, ServiceObject,
ServiceObjectGroup, PolicyRule, NATPolicyRule).

## Upgrade

```bash
pip install --upgrade nautobot-ssot-fortinet
sudo systemctl restart nautobot nautobot-worker
```

No schema changes. No new Jobs. No DiffSync attr changes.

## Reflection

Every release this morning was driven by the same lesson — *test the
path operators actually take*. v2.3-v2.7 covered live-validation of the
adapter writes. v2.9 covers what was the next gap: the Job lifecycle
itself. The test gap closes one more layer.

Real lesson: even when "live validation" is your discipline, identify
*which* live path you're validating. We were validating the adapter
path that our e2e scripts walk — not the Job path that real operators
walk. The two share most of the code but differ at the entry point,
which is exactly where this bug lived.
