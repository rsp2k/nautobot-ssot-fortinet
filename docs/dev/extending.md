# Extending the App

Common extensions, ordered roughly by how much code each requires.

## Adding an unmapped IP protocol number

If you see a service skipped with the log message
`"protocol-number N not mapped"`, extend
`IP_PROTOCOL_NUMBER_TO_NAME` in `src/nautobot_ssot_fortinet/utils/fortios.py`:

```python
IP_PROTOCOL_NUMBER_TO_NAME: dict[int, str] = {
    ...,
    254: "EXPERIMENTAL",   # add your mapping
}
```

The inverse table `IP_PROTOCOL_NAME_TO_NUMBER` rebuilds from this automatically. The new name must exist in `nautobot_firewall_models.choices.IP_PROTOCOL_CHOICES` — if not, you'll get a `ValidationError: not a valid choice` on push.

## Adding a FortiOS security mode → Nautobot auth choice

Extend `FORTIOS_VAP_SECURITY_MAP` in `utils/fortios.py`:

```python
FORTIOS_VAP_SECURITY_MAP: dict[str, str] = {
    ...,
    "your-new-security-string": "Nautobot Auth Choice",
}
```

The Nautobot side string must exactly match a value in `nautobot.wireless.choices.WirelessNetworkAuthenticationChoices`.

## Adding a new pull endpoint

Example: sync `cmdb/firewall/schedule` (firewall schedules).

1. **Add a DiffSync model class** in `diffsync/models/firewall.py`:

```python
class Schedule(DiffSyncModel):
    _modelname = "schedule"
    _identifiers = ("name",)
    _attributes = ("start", "end", "hostname", "vdom")
    name: str
    start: str
    end: str
    hostname: str
    vdom: str
```

2. **Add a Nautobot-side subclass** in `diffsync/models/nautobot_firewall.py` with `create`/`update`/`delete` against whatever model you're mapping to (here, you'd need to either add a custom field on existing AddressObject, or model it as a Tag, or skip persisting and just log).

3. **Register the model on both adapters** (`fortigate_firewall.py` and `nautobot_firewall.py`):

```python
schedule = Schedule
top_level = (..., "schedule")
```

4. **Implement `_load_schedules()`** on each adapter — FortiGate side calls `self.client.cmdb.firewall.schedule.get()`; Nautobot side queries the ORM with the appropriate scope.

5. **Add a fixture** in `tests/fixtures/firewall_schedule.json` and corresponding tests.

## Adding push support for a new object kind

The push direction requires three pieces:

1. **A new model subclass** in `diffsync/models/fortigate_target_firewall.py` with `create`/`update`/`delete` that build FortiOS payloads and call `adapter.client.cmdb.firewall.<endpoint>.create/update/delete`.
2. **Register the subclass** on `FortiGateFirewallTargetAdapter` in `diffsync/adapters/fortigate_firewall_target.py`.
3. **Inverse translation helpers** if any push-direction transform doesn't already have one. See the table in [architecture.md](architecture.md#translation-helpers).

Pattern reference: see how `FortiGateAddressObject` and `FortiGateServiceObject` are implemented.

## Adding a new live-status section

Extend `FortiGateLiveStatus.run()` in `jobs.py` to query additional `monitor/*` endpoints. The existing `_safe_get` helper handles flaky endpoints (returns `[]` on error rather than failing the whole Job).

```python
sessions = _safe_get(fgt, "api/v2/monitor/firewall/session?count=100")
# ... render to log + include in snapshot dict
```

## Adding wireless push direction

Wireless push isn't implemented yet but the scaffolding pattern is clear:

1. Add write-enabled subclasses in a new file `diffsync/models/fortigate_target_wireless.py` (mirroring `fortigate_target_firewall.py`).
2. Add a new adapter `diffsync/adapters/fortigate_wireless_target.py` inheriting from `FortiGateWirelessAdapter`.
3. Add a new Job `FortiGateWirelessDataTarget` in `jobs.py`.

For wireless specifically, the FortiGate REST shape for `vap` write differs from read in subtle ways (e.g., `passphrase` is write-only). Test carefully with `dry_run=True` first.

## Testing your extension

- **Unit tests** under `tests/` — match the existing patterns. For pure helpers, write tests in `test_utils_fortios.py`. For adapter behavior, follow `test_adapters_*.py`.
- **Fixture-based e2e** — add a fixture under `tests/fixtures/` and reference it in a `development/scripts/e2e_*.py` script.
- **Live e2e** — run against a real FortiGate via `make -C development e2e-live-*`. Always test with dry-run first if your change touches the push direction.

## See also

- [Architecture](architecture.md) — the conceptual model
- [Contributing](contributing.md) — how to submit changes back
