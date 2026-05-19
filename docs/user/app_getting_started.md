# Getting Started with the App

If you just want to sync a FortiGate into Nautobot in 10 minutes, this is for you.

## What you'll have at the end

After running the first sync, Nautobot's home dashboard will show synced
counts in the **Security**, **Wireless**, and **IPAM** panels — pulled
live from your FortiGate:

![Nautobot home dashboard showing synced FortiGate data](../assets/screenshots/nautobot-home-synced.jpg)

(Real screenshot from the dev stack — synced against a FortiWiFi-61E.)

## What you need before starting

- A Nautobot instance with the integration installed
  (see [`../admin/install.md`](../admin/install.md))
- A FortiGate with REST API access
- The IP address and an admin API token (or username + password) for the FortiGate

## Step 1 — Create credentials in Nautobot

In Nautobot's UI:

1. **Secrets → Secrets → Add** → create a Secret named e.g.
   `fgt-edge1 API token`, provider `environment-variable`, parameters
   `{"variable": "FGT_EDGE1_TOKEN"}`.
2. **Secrets → Secrets Groups → Add** → create a SecretsGroup named
   `fgt-edge1 creds`, add the Secret above with
   `Access Type=Generic, Secret Type=Token`.

Set the env var on the Nautobot worker:

```bash
export FGT_EDGE1_TOKEN="<your token>"
sudo systemctl restart nautobot-worker
```

## Step 2 — Create the ExternalIntegration

**Extensibility → External Integrations → Add**:

- **Name**: `fgt-edge1` (this name will prefix all synced object names)
- **Remote URL**: `https://10.0.0.1` (or your FortiGate's address)
- **Verify SSL**: True (or False for self-signed labs)
- **Timeout**: 30
- **Secrets Group**: select `fgt-edge1 creds`

## Step 3 — Enable + run the firewall pull Job

The integration registers **seven Jobs** visible at **Apps → Single Source
of Truth** (`/plugins/ssot/`) — three pull (data sources) for firewall /
wireless / device-and-interfaces, three push (data targets) for the
same three domains, plus a live-status diagnostic Job:

![SSoT dashboard showing all FortiGate sync Jobs](../assets/screenshots/ssot-dashboard.jpg)

Then:

1. **Extensibility → Jobs** — find **"FortiGate → Nautobot (firewall)"**
   → click the pencil → check **Enabled** → save
2. Click **Run Job** — you'll see the form below:

   ![Job runner form with ExternalIntegration picker and dry-run option](../assets/screenshots/job-runner-form.jpg)

3. Pick `fgt-edge1` from the **External integration** dropdown
4. Leave **Dryrun** checked for the first run — review the diff before
   any data hits Nautobot
5. Click **Run Job Now**

After a few seconds, browse to:
`/plugins/firewall/address-object/?q=fgt-edge1__`

You'll see all your FortiGate addresses, prefixed with `fgt-edge1__root__`.

## Step 4 — Re-run without dry-run

Now run the same Job again, uncheck **Dry run**, click submit. The Job
applies the diff for real. Browse the address-object list again — they
should still be there. Re-running the Job a third time should produce a
diff summary of `create=0, update=0, delete=0`.

## Step 5 — Repeat for wireless

If your FortiGate has wireless config:

1. Enable **"FortiGate → Nautobot (wireless)"**
2. Run it with the same ExternalIntegration
3. Browse `/wireless/wireless-networks/` to see synced SSIDs

## Step 6 — Sync the FortiGate as a Device (v3.0+)

To see your FortiGate appear in Nautobot's **Devices** list with its
interfaces, IPs, VLAN sub-interfaces, and static routes:

1. Pre-create the Nautobot scoping records (one-time setup):
    - **Manufacturer**: `Fortinet`
    - **DeviceType**: e.g. `FortiWiFi-61E`, `FortiGate-100F`
    - **Role**: e.g. `Firewall`
    - **Location**: wherever the FortiGate physically lives
    - **Status**: typically `Active` (Nautobot ships this)
2. Enable **"FortiGate → Nautobot (device + interfaces)"**
3. Run with the same ExternalIntegration plus the new ObjectVars
   (DeviceType / Role / Location / Status). Dryrun first.
4. Browse `/dcim/devices/` to see the FortiGate as a real Device with
   all its physical / aggregate / VLAN sub-interfaces

The sync also covers static routes (`router.static`) — they appear at
`/plugins/ssot-fortinet/static-routes/` (an app-owned UI, separate from
Nautobot core which doesn't yet have a first-class Route model).

## Step 7 — Check who's on your wifi right now

Enable **"FortiGate Live Status"** and run it with the ExternalIntegration.
The Job result page shows a table of connected wifi clients with their
MAC, IP, hostname (joined from DHCP leases), SSID, and data rate. A JSON
snapshot is attached for download.

## Step 8 — Edit-and-push workflow (optional)

If you want Nautobot to drive FortiGate config, three push directions
are available:

### Firewall push (v2.x)

Enable **"Nautobot → FortiGate (firewall)"**. Operators can edit
AddressObjects, AddressObjectGroups, ServiceObjects, ServiceObjectGroups,
PolicyRules, and NATPolicyRules — including the v2.6+ "edit the IP
value on a `vip_*_mapped` synth address" workflow that propagates to
the FortiGate VIP automatically.

### Wireless push (v2.x)

Enable **"Nautobot → FortiGate (wireless)"**. Create / update VAPs and
RadioProfiles from Nautobot.

### Device + interfaces push (v3.3+)

Enable **"Nautobot → FortiGate (device + interfaces)"**. Author VLAN
sub-interfaces and static routes in Nautobot's UI; push them to the
FortiGate. Pre-validation guards against the most common mistakes
(wrong-interface-parent, conflicting routes, etc.).

### General push workflow

1. Make your change in Nautobot's UI (or via REST / GraphQL)
2. Run the appropriate push Job with **Dryrun checked first** — review
   the diff
3. Apply when the diff looks correct
4. Verify on the FortiGate web UI that the change landed

Every push CRUD path is live-validated end-to-end against a real
FortiGate. See the e2e scripts in `development/scripts/e2e_push_*.py`
for the exact validation patterns.

!!! note "Known FortiOS REST limitations"
    - **VAP delete** via REST is blocked by a FortiOS circular
      quarantine-interface dependency. Use the FortiGate web UI's VAP
      delete wizard. Create and update work via push.
    - **Interface names** are silently truncated to 15 characters by
      FortiOS. Keep VLAN sub-interface names ≤ 15 chars or push/pull
      round-trips drift.
    - **AddressObjects referenced by routes** need `allow-routing:
      enable`. The v3.4+ push Job sets this automatically when an
      AddressObject is the destination of a route.

## What to do when something looks wrong

See [`../admin/troubleshooting.md`](../admin/troubleshooting.md).
