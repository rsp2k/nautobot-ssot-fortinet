# v2026.05.18.11 — Device + Interface sync (v3.0)

The first new capability since the v2.x stability work. **The FortiGate
now appears as a Nautobot `dcim.Device`** with its operator-meaningful
interfaces and IP assignments.

## What's new

A 5th DataSource Job: **"FortiGate → Nautobot (device + interfaces)"**.

Pull-only in v3.0. Wrong IPs on a FortiGate interface can disconnect
the appliance, so push direction (Nautobot → FortiGate for device
config) is intentionally deferred to v3.1+ with operator opt-in plus
pre-validation safeguards.

## What gets synced

| FortiOS interface type | → Nautobot | Notes |
|---|---|---|
| `physical` | `dcim.Interface` type=`1000base-t` | hardware ports (wan1, internal1-7, dmz, modem, etc.) |
| `aggregate` | `dcim.Interface` type=`lag` | e.g. `fortilink` |
| `hard-switch` | `dcim.Interface` type=`lag` | switch parent (`internal` on FortiWiFi-61E) |
| `switch` | `dcim.Interface` type=`lag` | soft switches (`lan`) |
| Interface IPs | `ipam.IPAddress` + auto-created parent `ipam.Prefix` | assigned via `interface.ip_addresses` |

The Device itself is created with operator-specified DeviceType, Role,
Location, and Status (similar to the wireless AP sync pattern).

## What's deliberately skipped

| Type | Reason |
|---|---|
| `vap-switch` | Already represented via `WirelessNetwork` sync (v2.0+) |
| `vlan` (auto-created `wqtn.X.Y`) | Mostly FortiOS-internal quarantine artifacts; defer to v3.1 |
| `tunnel` | VPN-specific; defer to a VPN-focused release |

## Form vars

| Field | Required | Description |
|---|---|---|
| **External integration** | ✓ | Picks which FortiGate to sync |
| **Vdom** | ✓ | FortiOS Virtual Domain (default "root") |
| **Device type** | ✓ | Nautobot DeviceType, e.g. `FortiWiFi-61E` |
| **Role** | ✓ | Nautobot Role, e.g. `Firewall` |
| **Location** | ✓ | Nautobot Location |
| **Status** | ✓ | Nautobot Status, typically `Active` |
| Delete records missing from source | optional | Default False = additive only |

Operators must pre-create the DeviceType / Role / Location / Status
records. The Job validates references exist before sync.

## New helper: `fortios_interface_ip_to_cidr`

The FortiOS dotted-mask format `"203.0.113.99 255.255.255.0"` means
two different things depending on the field:

- In `firewall.address.subnet`, it's the **network** the AddressObject
  represents → `fortios_subnet_to_cidr()` collapses to `203.0.113.0/24`
- In `system.interface.ip`, it's **this interface's host IP** → the new
  `fortios_interface_ip_to_cidr()` preserves the host: `203.0.113.99/24`

Caught during v3.0 live validation — the first sync produced phantom
network addresses; the fix produced the correct host IPs operators expect.

## Live-validated end-to-end against the dev FortiWiFi-61E

```
SOURCE (FortiGate):
  devices: 1  → ['fgt-dev']
  interfaces: 15
    - dmz         1000base-t  enabled=True  cidrs=['10.10.10.1/24']
    - fortilink   lag         enabled=True  cidrs=['10.255.1.1/24']
    - internal    lag         enabled=True
    - internal1-7 1000base-t  enabled=True
    - lan         lag         enabled=True  cidrs=['203.0.113.99/24']
    - modem       1000base-t  enabled=False
    - wan1, wan2  1000base-t  enabled=True
    - wqt.root    lag         enabled=True  cidrs=['10.253.255.254/20']

First-sync DIFF: {'create': 16, 'update': 0, 'delete': 0, 'no-change': 0}
Second-sync DIFF (idempotency check): {'create': 0, 'update': 0,
                                       'delete': 0, 'no-change': 16}
```

Idempotency confirmed — clean round-trip, no phantom drift.

## Known minor issue (deferred to v3.1)

`Device.serial` is empty after sync — the FortiOS serial-extraction path
in `_get_fortios_serial()` doesn't quite work with fortigate-api 2.0.8's
response envelope handling. **Not blocking** — the Device exists, all
interfaces sync correctly, IPs land properly. Operators who want the
serial populated can edit the Device manually until v3.1.

## Upgrade from v2026.05.18.10

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

Then in Nautobot's UI:

1. **Extensibility → Jobs** → find "FortiGate → Nautobot (device +
   interfaces)" → click pencil → check **Enabled** → save
2. **Apps → Single Source of Truth** → the new Job appears under Data
   Sources
3. Pre-create Nautobot DeviceType for your FortiGate model (under
   **Devices → Device Types → Add**), plus Role / Location / Status
   references if you don't have them already
4. Run the Job with **Dryrun checked** first, review the diff, then
   apply

After successful sync, browse **Devices → Devices** to see your
FortiGate as a real Nautobot Device with all its interfaces and IPs.

## What this unlocks

- Operators using other Nautobot SSoT integrations expect synced
  devices to appear in the Devices list — v3.0 closes that gap for
  FortiGate
- Cross-device GraphQL queries now include FortiGate interfaces and IPs
- Nautobot's existing DCIM tooling (cable connections, interface
  visualizations, IPAM rollup) works against synced FortiGate data
- Foundation for future SSoT integrations that want to cross-reference
  FortiGate interfaces (e.g., a future ACI or Meraki integration that
  needs to verify uplink interfaces match)

## What's next

Tracked for v3.1+:
- Push direction for device/interface config (with safety opt-in)
- Serial-number extraction fix
- VLAN sub-interface sync (with `parent_interface` resolution)
- Tunnel interface sync (in scope of a VPN-focused release)
- Static route sync (`router.static`)
