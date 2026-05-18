# Changelog

This project uses [CalVer](https://calver.org/) — versions are `YYYY.MM.DD`
representing the date of release. Same-day fixes use `YYYY.MM.DD.N`.

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
