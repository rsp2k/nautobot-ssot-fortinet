# Release Notes

Per-release notes for `nautobot-ssot-fortinet`. The project uses [CalVer](https://calver.org/) versioning (`YYYY.MM.DD`) — the date represents when the release was tested against external dependencies (Nautobot, nautobot-ssot, nautobot-firewall-models, fortigate-api, FortiOS), not internal API stability.

## Versions

- [v2026.05.18.9](version_2026.05.18.9.md) — **URGENT HOTFIX.** Job.run() instance-attr capture. Every prior version (v1.0–v2.8) crashed on first UI Job run. Upgrade immediately if affected.
- [v2026.05.18.8](version_2026.05.18.8.md) — Docs screenshots + dev-stack DNS modernization. No production-code changes.
- [v2026.05.18.7](version_2026.05.18.7.md) — Remaining CRUD paths live-validated end-to-end. DELETE status checking. Surfaces VAP-delete FortiOS limitation.
- [v2026.05.18.6](version_2026.05.18.6.md) — NAT update propagates from address-value-change. Editing a synth address's IP in Nautobot now updates the FortiGate VIP on push.
- [v2026.05.18.5](version_2026.05.18.5.md) — Policy + NAT push live-validated end-to-end. Round-trip stability fixes (/32 ipmask normalization, annotation dedup).
- [v2026.05.18.4](version_2026.05.18.4.md) — **Push direction hotfix.** Fixes latent `update(uid=)` bug across 10 callsites + 4 other live-only bugs. If you ran any push Job in v2.0–v2.2, upgrade.
- [v2026.05.18.3](version_2026.05.18.3.md) — wtp-profile CREATE via sibling aggregation (code path non-functional, fixed in v2.4).
- [v2026.05.18.2](version_2026.05.18.2.md) — PolicyRule + NATPolicyRule CREATE. All push directions full-CRUD (except wtp-profile create).
- [v2026.05.18.1](version_2026.05.18.1.md) — Wireless push + policy/NAT push (UPDATE/DELETE). 5 Jobs total.
- [v2026.05.18](version_2026.05.18.md) — Initial release. Bidirectional sync, live-validated against FortiWiFi-61E.

See [the project CHANGELOG](https://github.com/rsp2k/nautobot-ssot-fortinet/blob/main/CHANGELOG.md) for the canonical version history.
