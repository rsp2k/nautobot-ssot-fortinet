# Troubleshooting

## Authentication / connectivity

**Symptom:** Job log shows `ImproperlyConfigured: ExternalIntegration 'X' has no secrets_group set`

**Fix:** The ExternalIntegration's "Secrets Group" field is unset. Edit
the integration in **Extensibility → External Integrations** and select
the SecretsGroup you created.

---

**Symptom:** `ImproperlyConfigured: SecretsGroup 'X' has neither a TOKEN secret nor a USERNAME+PASSWORD pair`

**Fix:** The SecretsGroup exists but no Secret is associated with it, or
the env var that the env-var-provider Secret points at isn't set in the
Nautobot worker's environment. The error message includes the hint:
"If you're using the environment-variable provider, verify the named env
var is actually set inside the worker container."

To verify:

```bash
docker compose exec nautobot-worker env | grep FGT_
# or, on a bare-metal install:
sudo -u nautobot env | grep FGT_
```

---

**Symptom:** `requests.exceptions.ReadTimeout` partway through a sync, only in user/password auth mode

**Cause:** FortiOS enforces a per-admin concurrent-session limit (default
2–4). Each unwrapped REST call in user/pass mode triggers a fresh
`POST /logincheck`, exhausting the limit.

**Fix:** Already mitigated — the integration wraps API calls in
`with build_client(ext) as fgt:` so login happens once per sync. If you
still hit this, you're likely running multiple Jobs concurrently against
the same FortiGate admin user. Either:

- Create a dedicated API token (eliminates the `/logincheck` round-trip
  entirely), or
- Increase the FortiOS admin's `concurrent` session limit:
  `config system admin / edit <admin> / set concurrent <N>`

---

## Validation errors during push

**Symptom:** `KeyError: 'i'` deep in a Django ValidationError traceback

**Cause:** This is a bug in `nautobot-firewall-models.validators.validate_port` —
its error template uses `%(i)s` but its params dict uses `{"value": i}`,
so when the validator rejects an unexpected port form, Django can't even
stringify the error. The triggering condition is **spaces in the port
string**.

**Fix:** Already mitigated on push side — the integration converts
FortiOS-style space-separated port lists to comma-separated before
emitting them. If you're seeing this from a non-integration code path,
report to firewall-models upstream.

---

**Symptom:** `ValidationError: Value 'IP' is not a valid choice` on service push

**Cause:** FortiOS reports services like OSPF as `protocol: "IP"` with a
`protocol-number`. firewall-models expects the IANA-named protocol
(`OSPFIGP`, `GRE`, etc.), not `"IP"`.

**Fix:** Already mitigated — `fortios_service_ports()` in
`utils/fortios.py` translates protocol numbers to IANA names via the
`IP_PROTOCOL_NUMBER_TO_NAME` table. If you see a service with an unmapped
protocol number, add it to that table and re-run.

---

## Sync produces unexpected diffs

**Symptom:** Re-running a Job that previously synced cleanly now shows
`update` actions for records you haven't touched

**Possible causes:**

1. **Member ordering** — should not happen anymore (both adapters sort
   members), but if you see this on Policy/AddressGroup/ServiceGroup
   members, file a bug.
2. **Hand-edits in Nautobot** — additive mode preserves them; subsequent
   syncs will show their differences as updates. To restore the
   FortiGate's view of truth, run the pull Job with
   `delete_records_missing_from_source=True` (DANGEROUS — review the
   dry-run diff first).
3. **FortiGate config drift** — the FortiGate has changed since the last
   sync. This is the expected case — the diff shows you what changed.

---

## Live status Job

**Symptom:** "Wifi clients: 0" but you know clients are connected

**Possible causes:**

1. The FortiGate's `monitor/wifi/client` endpoint scope is different
   from the requested VDOM. Try setting `vdom` to whatever VDOM the
   wireless config lives in.
2. The REST API admin profile lacks read access to `monitor/*`. Verify
   in the FortiGate UI under the admin's profile permissions.
3. On the FortiWiFi-61E specifically, clients sometimes take 10–30s to
   appear in `monitor/wifi/client` after association. Re-run the Job.

---

## Live e2e harnesses

If `make e2e-live-firewall` or `make e2e-live-wireless` fails:

1. **Check connectivity first**: `docker compose exec nautobot-web curl -ksI --max-time 5 https://<fortigate-ip>/api/v2/monitor/system/status`
2. **Check the auth setup**: `make seed` re-prints the credential stack
   status; confirm env vars are seen as `✓ set`.
3. **Inspect the actual data shape**: many bugs come from FortiOS
   versions emitting fields the integration didn't expect. The e2e
   scripts wrap exceptions and print the offending record's MAC/name
   when possible. See the data-quirk diagnostic patterns in
   [`mapping.md`](mapping.md).

---

## "Nothing happened" when I clicked Run Job

Nautobot Jobs are **disabled by default** after installation. To enable:

1. Navigate to **Extensibility → Jobs**
2. Find the Job (e.g. "FortiGate -> Nautobot (firewall)")
3. Click the pencil icon → check **Enabled** → save
4. Now the **Run Job** button is active
