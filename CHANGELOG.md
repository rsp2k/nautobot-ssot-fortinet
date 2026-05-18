# Changelog

This project uses [CalVer](https://calver.org/) — versions are `YYYY.MM.DD`
representing the date of release. Same-day fixes use `YYYY.MM.DD.N`.

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
