# Installing the App in Nautobot

This page covers **install** and **configure** for `nautobot-ssot-fortinet` in your Nautobot environment.

## Prerequisites

| Component | Minimum | Verified against |
|---|---|---|
| Nautobot | 3.1 | 3.1.2 |
| nautobot-ssot | 4.2 | 4.2.2 |
| nautobot-firewall-models | 3.0 | 3.0.0 |
| fortigate-api | 2.0 | 2.0.8 |
| Python | 3.10 | 3.10–3.13 |
| FortiOS | 5.6 (token auth) or 4.x (user/pass) | 6.4–7.x |

## Install from PyPI

```bash
pip install nautobot-ssot-fortinet
```

Add to your Nautobot `PLUGINS` list:

```python
# nautobot_config.py
PLUGINS = [
    "nautobot_ssot",
    "nautobot_firewall_models",
    "nautobot_ssot_fortinet",
]
```

Run migrations + restart Nautobot:

```bash
nautobot-server migrate
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

## Configure a FortiGate target

Each FortiGate you want to sync is represented as one
`ExternalIntegration` record in Nautobot, pointing at the device's REST
endpoint and a `SecretsGroup` holding credentials.

### Step 1 — Create the API token on the FortiGate

In the FortiOS UI: **System → Administrators → Create New → REST API
Admin**. Set:

- **Profile**: a profile with read+write access to the cmdb/firewall and
  cmdb/wireless-controller endpoints (or `super_admin` for full access).
- **Trusted Hosts**: the IP your Nautobot worker connects from.
- **CORS**: not needed.

Copy the generated token — you'll only see it once.

### Step 2 — Create the Nautobot Secret + SecretsGroup

Navigate to **Secrets → Secrets → Add**:

- **Name**: `fgt-edge1 API token`
- **Provider**: `environment-variable` (or any provider you've configured)
- **Parameters**: `{"variable": "FGT_EDGE1_TOKEN"}`

Then **Secrets → Secrets Groups → Add**:

- **Name**: `fgt-edge1 creds`
- Add the Secret you just created with:
  - **Access Type**: `Generic` (mapped to HTTP)
  - **Secret Type**: `Token`

Set the environment variable in your Nautobot worker's environment:

```bash
export FGT_EDGE1_TOKEN="<the token from step 1>"
```

### Step 3 — Create the ExternalIntegration

**Extensibility → External Integrations → Add**:

- **Name**: `fgt-edge1` (will be the first segment of mangled names —
  must not contain `__`)
- **Remote URL**: `https://fgt-edge1.example.com` (or with port:
  `https://fgt:8443`)
- **Verify SSL**: True for production CA-signed certs; False for
  self-signed labs
- **Timeout**: 30 (seconds)
- **Secrets Group**: select the group you created

### Step 4 — Run the pull Job

**Extensibility → Jobs**:

1. Find **"FortiGate → Nautobot (firewall)"** and click **Enable**
2. Run it, selecting your `fgt-edge1` ExternalIntegration
3. Browse `/plugins/firewall/address-object/` to see synced addresses
   (named `fgt-edge1__root__<original>`)

Repeat for the wireless Job. The first run creates everything; subsequent
runs are no-ops unless drift exists.

## Username/password fallback (FortiOS < 5.6)

If your FortiOS doesn't support REST API tokens, create the SecretsGroup
with two secrets instead of one:

- One with **Secret Type**: `Username`, parameters
  `{"variable": "FGT_EDGE1_USERNAME"}`
- One with **Secret Type**: `Password`, parameters
  `{"variable": "FGT_EDGE1_PASSWORD"}`

The client factory automatically falls back to user/pass mode when no
TOKEN secret is found. Caveat: every API call triggers a `POST /logincheck`
in this mode — the integration uses a `with` context-manager to maintain
a single session per sync to avoid hitting per-admin concurrent-session
limits, but operators with very large config (hundreds of policies)
should consider upgrading FortiOS for token auth.

## Multi-FortiGate deployments

Each FortiGate gets its own `ExternalIntegration` record. The mangling
convention `<hostname>__<vdom>__<name>` ensures objects from different
devices don't collide in Nautobot's globally-unique name fields.

Constraints:

- The `ExternalIntegration.name` is used as the hostname segment of
  mangled names. It must not contain `__` (double underscore).
- ServiceObjects use a composite natural key `(ip_protocol, port, name)`
  and are NOT mangled. They form a globally-shared pool across all
  FortiGates synced to one Nautobot — if two FortiGates both define
  service "HTTP" as `TCP/80`, they collapse to the same Nautobot row.
  In additive-only mode (the default) this is safe; in destructive mode
  the operator must accept the cross-integration coupling.

## Push direction setup

Same configuration as pull — the push Job (`Nautobot → FortiGate (firewall)`)
uses the same ExternalIntegration. Make sure the REST API admin profile
on the FortiGate has **write** access (not just read) for `cmdb/firewall/*`
endpoints.

**Strong recommendation:** test the push direction in `dry_run` mode
first (Nautobot's built-in dry-run BooleanVar appears on every SSoT Job).
The push will compute diffs without applying them so you can review what
would change.

## Troubleshooting

See [`troubleshooting.md`](troubleshooting.md).
