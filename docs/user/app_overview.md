# App Overview

This document gives a top-level view of what the App does, who it's for, and which Nautobot facilities it leans on.

!!! note
    Throughout this documentation the terms "app" and "plugin" are used interchangeably.

## Description

`nautobot-ssot-fortinet` is a **bidirectional sync** between Nautobot and FortiGate devices, covering firewall configuration, wireless configuration, and live runtime state. It's built on the [`nautobot-app-ssot`](https://github.com/nautobot/nautobot-app-ssot) DiffSync framework, so each direction appears in the SSoT dashboard with the framework's diff/dry-run/sync-history machinery.

Unlike single-direction sync integrations, this app supports both arrows of the FortiGate↔Nautobot relationship — pull FortiGate config into Nautobot as the system of record, push operator-driven changes from Nautobot back to the device, plus a non-persistent "live status" Job for real-time observability.

## What it syncs

| FortiGate API surface | Nautobot model | Direction |
|---|---|---|
| `cmdb/firewall/address` | `nautobot_firewall_models.AddressObject` | both |
| `cmdb/firewall/addrgrp` | `nautobot_firewall_models.AddressObjectGroup` | both |
| `cmdb/firewall.service/custom` | `nautobot_firewall_models.ServiceObject` | both |
| `cmdb/firewall.service/group` | `nautobot_firewall_models.ServiceObjectGroup` | both |
| `cmdb/firewall/policy` | `nautobot_firewall_models.Policy + PolicyRule` | pull only |
| `cmdb/firewall/vip` | `nautobot_firewall_models.NATPolicy + NATPolicyRule` | pull only |
| `cmdb/wireless-controller/vap` | `nautobot.wireless.WirelessNetwork` | pull only |
| `cmdb/wireless-controller/wtp-profile` | `nautobot.wireless.RadioProfile` (one per radio) | pull only |
| `cmdb/wireless-controller/wtp` | `nautobot.dcim.Device` (role=AP) | pull, opt-in |
| `monitor/wifi/client`, `monitor/system/dhcp`, `monitor/network/arp` | rendered into a Job result table + JSON snapshot | observability |

See [External Interactions](external_interactions.md) for the complete FortiOS↔Nautobot field-by-field translation reference, including every vendor-specific quirk the integration handles.

## Audience (User Personas)

- **Network engineers** who already use Nautobot as a source of truth and want their FortiGate firewalls visible in the same place as their other gear.
- **MSPs** managing many FortiGates across customer sites — the integration's name-mangling scheme (`<hostname>__<vdom>__<original>`) lets one Nautobot instance cleanly hold many devices' configs without collisions.
- **Security teams** who want a single audit-friendly view of firewall rules across the fleet, exportable via Nautobot's REST/GraphQL APIs.
- **Automation operators** wiring Nautobot into broader IaC workflows: changes flow Nautobot UI → push Job → FortiGate, with full SSoT history on each sync.

## Direction & authority

The app supports **both directions**, but you usually pick one to be the source of truth per object kind:

- **FortiGate-as-source-of-truth** (most common starting point): operators continue to configure the FortiGate via its UI; the pull Jobs mirror state into Nautobot for inventory/audit/cross-device visibility.
- **Nautobot-as-source-of-truth** (more advanced): operators edit firewall objects in Nautobot's UI; the push Job propagates them to the FortiGate. This is the right model when you want Nautobot's RBAC + change history + GraphQL automation as the primary interface to the firewall.

Both directions default to **additive-only** — destructive deletes are opt-in per Job via a `delete_records_missing_from_source` BooleanVar.

## Nautobot facilities this App uses

- **SSoT framework** (`nautobot-app-ssot`) for the DiffSync engine, dashboard, and sync history.
- **`nautobot-firewall-models`** as the persistence target for all firewall objects (addresses, groups, services, policies, NAT).
- **Nautobot core wireless models** (3.1+) — `WirelessNetwork`, `RadioProfile`, optional `Device` for managed APs.
- **`ExternalIntegration`** + **`SecretsGroup`** for per-device URL + credential management. Credentials never live in `PLUGINS_CONFIG`.
- **Jobs framework** for the 4 Job classes the app registers.
