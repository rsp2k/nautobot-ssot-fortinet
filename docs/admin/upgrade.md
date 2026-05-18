# Upgrade

## In-place upgrade

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server migrate
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

The package is versioned in [CalVer](https://calver.org/) (`YYYY.MM.DD`) — the date in the version is when the release was tested against external dependencies (Nautobot, nautobot-ssot, nautobot-firewall-models, fortigate-api, FortiOS). Same-day fixes use `YYYY.MM.DD.N`.

## Breaking changes

See [CHANGELOG.md](https://github.com/rsp2k/nautobot-ssot-fortinet/blob/main/CHANGELOG.md) at the repo root for the full per-release notes. Breaking changes are flagged in each release section.

## After upgrade

1. Re-run the firewall and wireless pull Jobs against each `ExternalIntegration` — drift may exist between releases if FortiOS-mapping logic changed.
2. Review the SSoT dashboard for non-empty diffs that didn't exist before the upgrade — they indicate a translation rule changed.

## Downgrade

The integration does not currently require its own Django migrations (it writes to existing `nautobot-firewall-models` and Nautobot core wireless tables, not its own schema). Downgrade with:

```bash
pip install nautobot-ssot-fortinet==<previous-version>
sudo systemctl restart nautobot nautobot-worker
```

No `nautobot-server migrate` step is needed for downgrade.
