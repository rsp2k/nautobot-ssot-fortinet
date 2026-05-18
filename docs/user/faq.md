# FAQ

## Why are object names prefixed with `<hostname>__<vdom>__`?

Most `nautobot-firewall-models` objects enforce `unique=True` on `name`. Two FortiGates can legitimately each have an AddressObject named `WEB_SERVERS`, which would violate uniqueness if synced verbatim. The integration mangles names with `<hostname>__<vdom>__<original>` to keep them globally unique while preserving the original FortiOS name in the `description` field.

`ServiceObject` is the exception — it has a composite natural key `(ip_protocol, port, name)` and forms a shared pool across all synced FortiGates. Two FortiGates defining "HTTP" as `TCP/80` collapse to one Nautobot row.

## Why do some FortiOS services get skipped on sync?

Three FortiOS service patterns have no equivalent in `nautobot-firewall-models` and get skipped with a warning:

- `protocol: "ALL"` (used by the built-in `webproxy` service) — pseudo-protocol meaning "any IP protocol".
- `protocol: "IP"` with a `protocol-number` not in our [IP_PROTOCOL_NUMBER_TO_NAME table](external_interactions.md#ip-protocol-number-name-mapping-push-direction-needs-both-ways).
- Address types outside (ipmask, fqdn, iprange, ipaddress, interface-subnet) — e.g. geography, wildcard, dynamic, mac.

To support more cases, add to the relevant table in `src/nautobot_ssot_fortinet/utils/fortios.py`.

## What does "additive-only mode" mean?

Every sync Job has a `delete_records_missing_from_source` BooleanVar that defaults to `False`. In that default mode, the integration only creates and updates records — it never deletes records on the target side just because they're absent on the source side.

This is safer for the common case (hand-added records on the target side that should be preserved across syncs). To enable destructive sync, set the BooleanVar to `True` per-run.

## Why is my push Job failing on a service with port `"88 464"`?

It shouldn't be — the integration normalizes FortiOS space-separated multi-port strings to comma-separated form (Nautobot's `validate_port` only accepts commas). If you're hitting this, you may have a manually-created ServiceObject whose port contains a space; edit it to use commas instead.

## Why does the FortiGate password seem to be required every API call?

When using username + password auth (FortiOS pre-5.6), `fortigate-api` runs `POST /logincheck` per API call by default. The integration wraps API calls in `with build_client(ext) as fgt:` to maintain one session per sync, so this should only happen once. If you're hitting per-admin concurrent-session limits, switch to API token auth (FortiOS 5.6+) — tokens are stateless.

## How does the live status Job differ from the sync Jobs?

The **sync Jobs** read FortiOS `cmdb/*` endpoints (configuration intent — what the operator configured) and persist to Nautobot ORM. The **live status Job** reads FortiOS `monitor/*` endpoints (observed state — what's actually happening on the wire right now) and just logs to the Job result + attaches a JSON snapshot. Nothing persists.

## Can I sync FortiGate Devices into Nautobot's DCIM?

Not yet, but you can hand-create a DCIM `Device` record for the FortiGate and reference it in the `controller_device` field of a `Controller` record. The wireless Job's `ap_*` ObjectVars can auto-create FortiAP Devices, but the FortiGate itself is not yet auto-synced as a Device.

## What FortiOS versions are supported?

Verified against FortiOS 7.x on a FortiWiFi-61E. The upstream `fortigate-api` library targets 6.4.14 specifically; earlier versions may work but are not tested. Token auth requires FortiOS 5.6+.

## See also

- [Troubleshooting](../admin/troubleshooting.md) — common error symptoms + fixes
- [External Interactions](external_interactions.md) — the full field-by-field mapping reference
