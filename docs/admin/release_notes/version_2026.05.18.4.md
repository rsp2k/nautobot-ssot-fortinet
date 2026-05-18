# v2026.05.18.4 — Push direction hotfix: actually-works edition (v2.3)

Hotfix for v2.2 (2026.05.18.3) and **multiple latent bugs from v2.0+**.
If you ran any push Job in v2.0–v2.2 and saw "Created/Updated successfully"
logs, your sync most likely did nothing on the FortiGate — this release
fixes that.

## The story

The user asked one question: *"did you test the new code on the
live FortiGate?"* The honest answer was no, only unit tests with mocks.

Live testing surfaced a stack of bugs that mock-based unit tests can't
catch:

1. **`Connector.update(uid=..., data=...)` raised `TypeError` on every
   call** — fortigate-api's signature is `update(self, data)` only; the
   uid lives inside data. `MagicMock()` accepts any kwargs silently, so
   unit tests passed. **Bug present in 10 callsites across firewall and
   wireless target adapters since v2.0.**
2. **`channel: ["1", "6", "11"]` rejected by FortiOS with HTTP 500.** The
   correct shape is `channel: [{"chan": "1"}, {"chan": "6"}, ...]` — list
   of objects, not flat list. Empirically probed against FortiOS v7.0.14.
3. **`comment` with parentheses rejected as XSS** (error -173: "The string
   contains XSS vulnerability characters"). Default wtp-profile comment
   used `(N radios)` — changed to `[N radios]`.
4. **No HTTP status checking on create/update responses.** All FortiOS
   rejections (500 + error code) were silently dropped. DiffSync's
   "Created successfully" log message only describes its in-memory store,
   not the actual REST call result.
5. **Verification scripts used `.get(uid=...)`** which doesn't filter —
   `Connector.get(**kwargs)` pops `kwargs[self.uid]` (which for most
   endpoints is `"name"`, not `"uid"`). The buggy call fetched ALL
   records and `[0]` picked an unrelated one as "the verified record."
   This made every "live validated" claim in v1.0–v2.2 a false positive
   for anything beyond bulk pull/load shape.

## What changed

### `check_fortios_response()` helper

New helper in `utils/fortios.py` that raises `FortiOSAPIError` on
non-200 responses, including the FortiOS `status`, `error`, and
`cli_error` fields plus a label identifying the failed call. All 17
create/update callsites now check status. Catching this in model code
means failures are loud instead of silent.

### Channel format fix

`_radio_payload()` now builds the correct FortiOS schema:

```python
# Pre-v2.3 (broken, silent HTTP 500):
payload["channel"] = ["1", "6", "11"]
# v2.3:
payload["channel"] = [{"chan": "1"}, {"chan": "6"}, {"chan": "11"}]
```

### XSS-safe default comment

`FortiGateRadioProfile.create()` now uses brackets in the auto-generated
comment: `Created from Nautobot via nautobot-ssot-fortinet sync [2 radios]`.

### Verification scripts fixed

`e2e_push_validate.py` and `e2e_push_wtp_profile.py` now use the correct
`.get(name=...)` filter. Past verification logs that said "✓ FortiGate
has X" with X equal to the actual injected name were false positives —
re-validate any prior claims if you depend on them.

### v2.2 wtp-profile create — confirmed working end-to-end

After v2.3 fixes, the live FWF-61E test passes:

```
[3/4] Verify on FortiGate side...
  ✓ FortiGate has wtp-profile 'e2e-wtp-test'
    comment:       'Created from Nautobot via nautobot-ssot-fortinet sync [2 radios]'
    radio-1 band:  '802.11n,g-only'
    radio-2 band:  '802.11ac'
  ✓ Both radios populated — sibling aggregation worked end-to-end
```

(FortiOS normalizes `802.11ax-5G` to `802.11ac` on the FWF-61E because
that's the highest-band the hardware supports — hardware-appropriate.)

### Tests

- **193 unit tests** (was 188 in v2.2). +4 for `check_fortios_response`,
  +1 regression guard using `MagicMock(spec=Connector)` so the
  `update(uid=...)` bug can't recur.

## Upgrade from v2026.05.18.3

```bash
pip install --upgrade nautobot-ssot-fortinet
nautobot-server collectstatic --no-input
sudo systemctl restart nautobot nautobot-worker
```

No new Jobs (still 5). No DiffSync attr changes. No schema changes.

If you ran push Jobs in v2.0–v2.2 and saw "Updated successfully" but
the FortiGate state didn't match what you expected — that was the
`uid=` bug. Re-run the pull Job to refresh Nautobot's view, compare with
your intended state, then re-run push — the writes will actually land now.

## Reflection

Two lessons worth keeping:

1. **`MagicMock` is a footgun for keyword-argument signature mismatches.**
   Use `MagicMock(spec=ConcreteClass)` for any unit test where the kwargs
   you pass must match a real API's signature.
2. **A passing verification test isn't validation if the verification call
   itself is buggy.** The `.get(uid=...)` pattern matched the
   `.delete(uid=...)` pattern and was assumed correct by analogy — but
   the methods have different signatures (`get(**kwargs)` vs
   `delete(uid="", **kwargs)`). Live, end-to-end, "look at the actual
   record on the device" tests would have caught this in v1.0.
