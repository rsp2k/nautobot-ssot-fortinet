# Architecture

The integration is built on the [DiffSync](https://diffsync.readthedocs.io/) library, wrapped by the Nautobot SSoT framework. This page sketches how the moving parts fit together.

## Layered design

```
┌────────────────────────────────────────────────────────────────┐
│  Jobs (jobs.py)                                                │
│    FortiGateFirewallDataSource    FortiGateWirelessDataSource  │
│    FortiGateFirewallDataTarget    FortiGateLiveStatus          │
└────────────────────────┬───────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────┐
│  Adapters (diffsync/adapters/)                                 │
│    Source = "what is on the source side now"  (load() method)  │
│    Target = "what is on the target side now"  (load() + CRUD)  │
└──────┬──────────────────────┬──────────────────────────────────┘
       │                      │
       ▼                      ▼
┌──────────────────┐    ┌──────────────────┐
│ Pure DiffSync    │    │ Per-target CRUD  │
│ model classes    │◄───│ subclasses       │
│ (firewall.py,    │    │ (nautobot_*.py,  │
│  wireless.py)    │    │  fortigate_      │
│                  │    │  target_*.py)    │
└──────────────────┘    └──────────────────┘
       ▲                      ▲
       │                      │
       └──────────┬───────────┘
                  │
                  ▼
┌────────────────────────────────────────────────────────────────┐
│  Translation helpers (utils/fortios.py)                        │
│    Pure functions; no I/O. Inverse pairs for round-tripping.   │
└────────────────────────────────────────────────────────────────┘
```

## DiffSync vocabulary recap

- **Model class** — describes one kind of object (`AddressObject`, `WirelessNetwork`, ...). Identifies records by `_identifiers` and tracks state via `_attributes`. CRUD methods (`create`/`update`/`delete`) are called by DiffSync when a sync produces a diff.
- **Adapter** — a container of model instances loaded from one side of a sync. Has a `load()` method that populates the store from the underlying source (ORM, REST API, fixture file, etc.).
- **Diff** — the result of comparing two adapters; records on one side but not the other become create/delete actions, attribute mismatches become updates.
- **Sync** — applying the diff by calling the appropriate model methods on the target adapter.

## Per-target CRUD subclasses

The same DiffSync model class hierarchy is reused across all sync directions:

```
AddressObject (vendor-neutral, in diffsync/models/firewall.py)
├── NautobotAddressObject (in nautobot_firewall.py)
│     create/update/delete write to nautobot-firewall-models ORM
└── FortiGateAddressObject (in fortigate_target_firewall.py)
      create/update/delete write to FortiGate REST API via fortigate-api
```

When the **pull** Job runs:
- Source adapter (FortiGate) uses base `AddressObject` (no CRUD — read-only by design)
- Target adapter (Nautobot) uses `NautobotAddressObject` (CRUD against ORM)

When the **push** Job runs:
- Source adapter (Nautobot) uses `NautobotAddressObject` (CRUD against ORM — but only `load()` is exercised since Nautobot is the read-only source on push)
- Target adapter (FortiGate) uses `FortiGateAddressObject` (CRUD against REST API)

This symmetry is the core architectural payoff of DiffSync: one model hierarchy, two transport implementations, two direction Jobs.

## Name mangling

FortiOS object names are unique *within a FortiGate-VDOM scope* but not globally. Most Nautobot firewall-models fields enforce `unique=True` on `name`. The integration bridges this with:

```
mangled_name = f"{hostname}__{vdom}__{original_name}"
```

The hostname segment is the `ExternalIntegration.name`. The original FortiOS name is preserved in the `description` field for human readability, prefixed `"<original>: "`.

Exceptions:
- `ServiceObject` — has composite NK `(ip_protocol, port, name)`, no mangling needed.
- AP `Device` (when push synced) — uses serial number as identifier.

## Translation helpers

Every FortiOS↔Nautobot field-shape mismatch is encapsulated in a pure function in `utils/fortios.py`. The pull direction discovered most of them; the push direction usually needs an inverse:

| Pull helper | Push helper |
|---|---|
| `fortios_subnet_to_cidr()` | `_cidr_to_fortios_subnet()` (in `fortigate_target_firewall.py`) |
| `_normalize_port_separators()` (space→comma) | `denormalize_port_separators()` (comma→space) |
| `fortios_service_ports()` (FortiOS shape → ip_protocol+port) | `build_fortios_service_payload()` (ip_protocol+port → FortiOS shape) |
| `IP_PROTOCOL_NUMBER_TO_NAME` (89 → OSPFIGP) | `IP_PROTOCOL_NAME_TO_NUMBER` (OSPFIGP → 89) |
| `fortios_security_to_auth()` | (no inverse; wireless push not yet implemented) |
| `fortios_band_to_frequency()` | (no inverse; wireless push not yet implemented) |

Round-trip identity (`pull(push(x)) == x`) is tested via `TestServiceRoundTrip` in `tests/test_utils_fortios.py`.

## Adapter inheritance for push direction

The push direction reuses the read-only pull adapter's `load()` method but swaps the model classes for write-enabled ones:

```python
class FortiGateFirewallTargetAdapter(FortiGateFirewallAdapter):
    address_object = FortiGateAddressObject
    address_object_group = FortiGateAddressObjectGroup
    service_object = FortiGateServiceObject
    service_object_group = FortiGateServiceObjectGroup
    # load() inherited — reads FortiGate current state to compute accurate diffs
```

This is the cleanest expression of "same machinery, different writes": one adapter class hierarchy, parameterized by the model classes registered as attributes.

## What's deliberately NOT modeled

- **Policies and NAT have no push counterparts yet.** Their 12+ M2M relationships make them a larger effort than addresses/services.
- **No custom Nautobot models.** Everything lands in `nautobot-firewall-models` or Nautobot core wireless. This makes the integration disposable — uninstalling removes the Jobs but leaves the data in place.
- **No async/concurrent syncs.** Each Job is sequential. FortiOS REST has rate limits and per-admin concurrent-session caps; sequential is safer.
- **No Capirca integration.** The original v1 plan considered it as a stretch goal; not in scope.

## See also

- [Extending the App](extending.md) — how to add a new object kind or address type
- [External Interactions](../user/external_interactions.md) — the complete FortiOS↔Nautobot field reference
