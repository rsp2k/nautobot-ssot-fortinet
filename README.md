# nautobot-ssot-fortinet

> **Bidirectional Nautobot ↔ FortiGate sync** for firewall, wireless,
> and live runtime state — built on the
> [Nautobot SSoT framework](https://docs.nautobot.com/projects/ssot/en/latest/).

[![Tests](https://img.shields.io/badge/tests-174%20passing-brightgreen)](#testing)
[![Live-validated](https://img.shields.io/badge/live--validated-FortiWiFi--61E-blue)](#what-this-does)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/rsp2k/nautobot-ssot-fortinet/blob/main/pyproject.toml)
[![Nautobot](https://img.shields.io/badge/nautobot-3.1%2B-orange)](https://nautobot.com/)

## What this does

Four Nautobot Jobs, each handling one direction of the FortiGate↔Nautobot relationship:

| # | Job | Direction | Object kinds |
|---|---|---|---|
| 1 | **FortiGate → Nautobot (firewall)** | Pull | Addresses, address groups, services, service groups, policies + rules, NAT (VIPs) |
| 2 | **FortiGate → Nautobot (wireless)** | Pull | WirelessNetworks (SSIDs), RadioProfiles (radios), optional AP Devices |
| 3 | **FortiGate Live Status** | Observability | Real-time wifi clients, DHCP leases, ARP table — joined by MAC |
| 4 | **Nautobot → FortiGate (firewall)** | Push | Address objects (all 4 types), address groups, services, service groups |

All Jobs are **idempotent** (re-running produces zero diffs when nothing
changed) and **additive-only by default** (destructive deletes opt-in via
a per-Job BooleanVar).

## How it works

```
                    ┌────────────┐
                    │  Nautobot  │
                    │            │
                    └─────┬──────┘
                       ↑  │
              pull (2) │  ▼ push (1)
                       │
                    ┌────────────┐    monitor/* (live state)
                    │  FortiGate │ ────────────────────────→ Job 3
                    └────────────┘
```

- **Pull Jobs** read `cmdb/*` endpoints and write to Nautobot ORM
  (firewall-models + core wireless). State on the FortiGate is the source of
  truth; Nautobot mirrors it.
- **Push Job** reads from Nautobot ORM and writes to FortiGate `cmdb/*`
  endpoints. Operators edit firewall objects in Nautobot's UI; the push
  Job propagates them to the device.
- **Live status Job** queries `monitor/*` endpoints for real-time
  observed state (connected clients, leases, ARP) and renders them in
  the Job result page + downloadable JSON snapshot.

## Quick start

```bash
# 1. Bring up the dev stack
cp development/.env.example development/.env
# edit development/.env — set NAUTOBOT_SECRET_KEY + FortiGate credentials
make -C development up

# 2. Wait ~60s, then seed the ExternalIntegration + SecretsGroup
make -C development seed

# 3. Open https://ssot-fortinet-dev.local/ , enable the 4 Jobs in
#    /extras/jobs/ , then run them from /plugins/ssot/
```

For full installation steps including production deployment, see
[`docs/admin/install.md`](https://github.com/rsp2k/nautobot-ssot-fortinet/blob/main/docs/admin/install.md).

## Live-validated against real hardware

This integration was validated end-to-end against a **FortiWiFi-61E**
during development. The live e2e harness (`make e2e-live-firewall` and
`make e2e-live-wireless`) connects to a real FortiGate, syncs all object
kinds, and asserts the second run produces zero diffs (full idempotency).

Real-world quirks discovered & handled:

| FortiOS reality | Translation |
|---|---|
| `interface-subnet` address type | Treated as `ipmask` (FortiOS already resolves to CIDR) |
| Space-separated multi-port (`"88 464"` Kerberos) | Normalized to comma (`"88,464"`) for firewall-models' validator |
| `ICMP6` protocol | Mapped to `IPv6-ICMP` (IANA name used by firewall-models) |
| `'513:512-1023'` src-port qualifier (RLOGIN/RSH) | Source-port stripped, destination kept |
| `protocol: "ALL"` pseudo-protocol (webproxy) | Skipped — no Nautobot equivalent |
| `protocol: "IP"` + `protocol-number: N` | Mapped to named IANA protocol (e.g. 89 → `OSPFIGP`) |
| FortiOS WTP-profile mode vs Nautobot WirelessNetwork mode | Most-common platform-mode per VAP wins |
| FortiOS radio band strings (`802.11ax-5G`) | Pattern-matched to `2.4GHz`/`5GHz`/`6GHz` enum |
| FortiOS VAP `security: "wep128"` | Lossy mapping → `Open` with annotation in description |

See [`docs/user/external_interactions.md`](https://github.com/rsp2k/nautobot-ssot-fortinet/blob/main/docs/user/external_interactions.md) for the complete
field-by-field reference.

## What's required

- **Nautobot 3.1+** (for the wireless models)
- **nautobot-ssot 4.2+** (SSoT framework)
- **nautobot-firewall-models 3.0+** (firewall object target)
- **fortigate-api 2.0+** (REST client; verified against FortiOS 6.4–7.x)
- **Python 3.10–3.13**

See [`docs/admin/compatibility_matrix.md`](https://github.com/rsp2k/nautobot-ssot-fortinet/blob/main/docs/admin/compatibility_matrix.md) for the
detailed compatibility matrix and version pinning rationale.

## What's _not_ in scope (yet)

- **Policy/NAT push** — pull works; push for those is a future iteration
  (the M2M dependency graph is more complex than addresses/services)
- **Source NAT** (FortiOS `ippool`) — pull only does Destination NAT (VIPs)
- **IPv6 addresses** (`firewall/address6`) — IPv4 only for v1
- **Multi-VDOM aware UI** — Jobs scope by VDOM, but the per-VDOM dropdown
  in the SSoT dashboard is a Nautobot-side enhancement, not in this app

## Project layout

```
src/nautobot_ssot_fortinet/
├── clients/fortigate.py        ExternalIntegration → FortiGateAPI factory
├── diffsync/
│   ├── models/
│   │   ├── firewall.py         Vendor-neutral DiffSync models
│   │   ├── wireless.py         Vendor-neutral DiffSync models
│   │   ├── nautobot_firewall.py     Nautobot-side CRUD (pull target)
│   │   ├── nautobot_wireless.py     Nautobot-side CRUD (pull target)
│   │   └── fortigate_target_firewall.py   FortiGate-side CRUD (push target)
│   └── adapters/
│       ├── fortigate_firewall.py       Pull source
│       ├── fortigate_wireless.py       Pull source
│       ├── fortigate_firewall_target.py    Push target (read state + write CRUD)
│       ├── nautobot_firewall.py        Pull target (= push source)
│       └── nautobot_wireless.py        Pull target
├── jobs.py                     4 Job classes
└── utils/fortios.py            Translation helpers (subnet→CIDR, security→auth, etc.)
```

## Testing

```
$ pytest -q
174 passed in 0.57s
```

Unit tests use a `MagicMock`-stubbed Django (per `tests/conftest.py`) so they
run in milliseconds without bootstrapping Nautobot. Integration coverage
is provided by the live e2e harnesses in `development/scripts/` — those
require a real FortiGate.

## Status

**v0.x — bidirectional sync working against real hardware.** Ready for
controlled production use against a single FortiGate; multi-device
deployments should validate carefully (the name-mangling convention
handles cross-device uniqueness, but multi-VDOM hostnames with `__`
characters need attention).

Roadmap: see [`CHANGELOG.md`](https://github.com/rsp2k/nautobot-ssot-fortinet/blob/main/CHANGELOG.md).

## License

Apache-2.0.

## Author

Ryan Malloy — `ryan@supported.systems`
