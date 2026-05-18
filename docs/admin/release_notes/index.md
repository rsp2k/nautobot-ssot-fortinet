# Release Notes

Per-release notes for `nautobot-ssot-fortinet`. The project uses [CalVer](https://calver.org/) versioning (`YYYY.MM.DD`) — the date represents when the release was tested against external dependencies (Nautobot, nautobot-ssot, nautobot-firewall-models, fortigate-api, FortiOS), not internal API stability.

## Versions

- [v2026.05.18.4](version_2026.05.18.4.md) — **Push direction hotfix.** Fixes latent `update(uid=)` bug across 10 callsites + 4 other live-only bugs. If you ran any push Job in v2.0–v2.2, upgrade.
- [v2026.05.18.3](version_2026.05.18.3.md) — wtp-profile CREATE via sibling aggregation (code path non-functional, fixed in v2.4).
- [v2026.05.18.2](version_2026.05.18.2.md) — PolicyRule + NATPolicyRule CREATE. All push directions full-CRUD (except wtp-profile create).
- [v2026.05.18.1](version_2026.05.18.1.md) — Wireless push + policy/NAT push (UPDATE/DELETE). 5 Jobs total.
- [v2026.05.18](version_2026.05.18.md) — Initial release. Bidirectional sync, live-validated against FortiWiFi-61E.

See [the project CHANGELOG](https://github.com/rsp2k/nautobot-ssot-fortinet/blob/main/CHANGELOG.md) for the canonical version history.
