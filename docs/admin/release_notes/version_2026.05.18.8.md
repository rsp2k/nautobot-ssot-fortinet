# v2026.05.18.8 — Docs screenshots + dev-stack DNS modernization (v2.7)

Documentation polish + dev-stack convenience. **No production code
changes** — every file under `src/` is byte-identical to v2.7.

## Docs

Three real screenshots from the dev stack are now woven into
`docs/user/app_getting_started.md`. The new narrative flow:

1. **"What you'll have at the end"** (hero shot) — Nautobot home
   dashboard with synced counts visible in Security / Wireless / IPAM
   panels. Shows operators the visible result *before* they invest time
   in setup.
2. **"Where to find the Jobs"** — SSoT dashboard showing all four sync
   Jobs (2 pull + 2 push) and the diagnostic live-status Job. Makes the
   "bidirectional" claim visually concrete.
3. **"What the form looks like"** — Job runner page with the External
   Integration picker, Dryrun checkbox, and Vdom field visible.

`docs/user/app_use_cases.md` Use Case 3 now lists every model that
supports edit-and-push, including v2.6's edit-synth-address propagation
and v2.7's VAP-delete REST limitation.

## Dev stack changes

### DNS modernization

`DOMAIN` in `development/.env` changed from `ssot-fortinet-dev.local`
to `ssot-fortinet-dev.l.warehack.ing`. The warehack.ing wildcard DNS
resolves automatically and Caddy gets a real ACME cert via Vultr
DNS-01 — **no more `/etc/hosts` edits required.**

### Loopback fallback for browser automation

Added `127.0.0.1:8080:8080` port mapping on the dev web container.
Useful for browser automation (Playwright, headless captures) in
environments where the wildcard DNS or ACME cert isn't available.

```yaml
nautobot-web:
  ports:
    - "127.0.0.1:8080:8080"   # ← new
```

Bound to loopback only, so it's not exposed beyond the host.
`ALLOWED_HOSTS` already included `localhost 127.0.0.1` so no Nautobot
config change was needed. Caddy remains the canonical ingress for
`${DOMAIN}`.

## Upgrade from v2026.05.18.7

```bash
pip install --upgrade nautobot-ssot-fortinet
```

No DB migration. No Job changes. No production-code changes. The RTD
docs site will auto-rebuild from the new tag.

If you run the dev stack yourself:

```bash
cd development
# Edit .env to update DOMAIN if you want the warehack.ing convention
make up   # recreates the web container with the new label + port mapping
```

## What's not in this release (queued for later)

Additional screenshots that would be valuable but require successful
sync state at capture time:

- Synced PolicyRule detail page showing the `[srcintf=lan dstintf=wan1]`
  annotation in the description field
- Synth VIP AddressObject pair (`vip_X_ext` + `vip_X_mapped`) showing
  the synthesis convention
- Pre-push dry-run diff page with pending changes visible
- A 3-shot sequence of the new v2.6 edit-synth-address workflow

These need the dev stack to have actually run a successful sync against
real data before the screenshots can be taken — out of scope for this
release. Future v2.9 will pick them up.
