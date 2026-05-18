# External Interactions

The complete field-by-field translation reference. Every quirk in here
emerged from live FortiOS data during development and is implemented in
`src/nautobot_ssot_fortinet/utils/fortios.py`.

## Object kind mapping

| FortiOS endpoint | Nautobot model | Mapping notes |
|---|---|---|
| `cmdb/firewall/address` | `nautobot_firewall_models.AddressObject` | 4-way type discriminator (see below) |
| `cmdb/firewall/addrgrp` | `nautobot_firewall_models.AddressObjectGroup` | Members resolved by mangled name |
| `cmdb/firewall.service/custom` | `nautobot_firewall_models.ServiceObject` | Composite NK `(ip_protocol, port, name)` |
| `cmdb/firewall.service/group` | `nautobot_firewall_models.ServiceObjectGroup` | Members are composite NK tuples |
| `cmdb/firewall/policy` | `nautobot_firewall_models.Policy + PolicyRule` | One Policy per (FortiGate, VDOM); each FortiOS policy entry → one PolicyRule |
| `cmdb/firewall/vip` | `nautobot_firewall_models.NATPolicy + NATPolicyRule` | DNAT only; synthesizes AddressObjects + ServiceObjects for `extip`/`mappedip` |
| `cmdb/wireless-controller/vap` | `nautobot.wireless.WirelessNetwork` | One per VAP; SSID + auth + mode |
| `cmdb/wireless-controller/wtp-profile` | `nautobot.wireless.RadioProfile` (fan-out) | One per (profile, radio-N) pair |
| `cmdb/wireless-controller/wtp` | `nautobot.dcim.Device` (role=AP) | **Optional** — only if `ap_*` Job vars provided |

## Address types

FortiOS `firewall/address.type` value → Nautobot AddressObject FK field:

| FortiOS type | Nautobot FK populated | Value form |
|---|---|---|
| `ipmask` | `prefix` (`ipam.Prefix`) | CIDR — converted from `"10.0.0.0 255.255.255.0"` form |
| `interface-subnet` | `prefix` (`ipam.Prefix`) | FortiOS already resolves to CIDR in the `subnet` field |
| `fqdn` | `fqdn` (`firewall_models.FQDN`) | FQDN string as-is |
| `iprange` | `ip_range` (`firewall_models.IPRange`) | `start-ip` + `end-ip` from FortiOS |
| `ipaddress` | `ip_address` (`ipam.IPAddress`) | Single host extracted from `subnet` field |
| `geography`, `wildcard`, `mac`, `dynamic` | — | **Skipped** with warning — no Nautobot equivalent |

## Service / IP-protocol mapping

FortiOS service `protocol` field → Nautobot `ServiceObject.ip_protocol`:

| FortiOS `protocol` | Nautobot `ip_protocol` | Notes |
|---|---|---|
| `TCP/UDP/SCTP` (with `tcp-portrange`) | `TCP` | Port copied verbatim, except spaces converted to commas |
| `TCP/UDP/SCTP` (with `udp-portrange`) | `UDP` | Same |
| `TCP/UDP/SCTP` (with `sctp-portrange`) | `SCTP` | Same |
| `TCP/UDP/SCTP` (multiple subfields populated) | `TCP` | TCP wins by convention |
| `ICMP` | `ICMP` | `icmptype` → port |
| `ICMP6` | `IPv6-ICMP` | FortiOS uses short name; firewall-models uses IANA name |
| `IP` + `protocol-number=N` | name per `IP_PROTOCOL_NUMBER_TO_NAME` (e.g. 89 → `OSPFIGP`) | Empty port |
| `ALL` | — | **Skipped** — no Nautobot equivalent (FortiOS pseudo-protocol used for `webproxy`) |

### IP protocol number → name mapping (push direction needs both ways)

| Number | Name |
|---|---|
| 1 | `ICMP` |
| 2 | `IGMP` |
| 4 | `IPv4` |
| 6 | `TCP` |
| 8 | `EGP` |
| 17 | `UDP` |
| 41 | `IPv6` |
| 47 | `GRE` |
| 50 | `ESP` |
| 51 | `AH` |
| 88 | `EIGRP` |
| 89 | `OSPFIGP` |
| 103 | `PIM` |
| 112 | `VRRP` |
| 115 | `L2TP` |
| 132 | `SCTP` |

Protocol numbers outside this list cause the service to be skipped with a
warning. Add to `IP_PROTOCOL_NUMBER_TO_NAME` in `utils/fortios.py` if you
need more.

## Port format

| FortiOS form | Nautobot form | Reason |
|---|---|---|
| `"443"` | `"443"` | Single port — identical |
| `"8000-8099"` | `"8000-8099"` | Range — identical |
| `"88 464"` (space-separated, KERBEROS) | `"88,464"` (comma-separated) | `nautobot_firewall_models.validators.validate_port` splits on commas only — and emits a broken error template (`KeyError: 'i'`) if it sees spaces |
| `"513:512-1023"` (dst:src qualifier, RLOGIN) | `"513"` | Nautobot ServiceObject has no source-port concept |

## Policy action

FortiOS `action` → firewall-models `action` (lowercase enum):

| FortiOS | Nautobot | Notes |
|---|---|---|
| `accept` / `permit` / `allow` | `allow` | Clean mapping |
| `deny` | `deny` | Clean mapping |
| `drop` | `drop` | Clean mapping |
| `ipsec` | `allow` | Lossy — IPsec tunnel-routing intent lost; annotation added to description |
| anything else | `deny` | Defensive fallback; annotation added to description |

## Wireless: auth modes

FortiOS `vap.security` → Nautobot `WirelessNetworkAuthenticationChoices`:

| FortiOS | Nautobot | Lossy? |
|---|---|---|
| `open` | `Open` | No |
| `wpa-personal`, `wpa-personal+pmf`, `wpa2-only-personal` | `WPA2 Personal` | No |
| `wpa-enterprise`, `wpa2-only-enterprise` | `WPA2 Enterprise` | No |
| `wpa3-sae`, `wpa3-sae-transition` | `WPA3 SAE` | No |
| `wpa3-personal`, `wpa3-only-personal` | `WPA3 Personal` | No |
| `wpa3-enterprise`, `wpa3-only-enterprise` | `WPA3 Enterprise` | No |
| `wpa3-enterprise-192`, `wpa3-only-enterprise-192` | `WPA3 Enterprise 192Bit` | No |
| `owe`, `osen` | `Enhanced Open` | No |
| `wep64`, `wep128` | `Open` | **Yes** — WEP not supported by Nautobot; annotation added |
| `captive-portal` | `Open` | **Yes** — captive portal context lost |
| unknown | `Open` | **Yes** — defensive fallback with annotation |

## Wireless: radio band

FortiOS `wtp-profile.radio-N.band` → Nautobot `RadioProfileFrequencyChoices`:

| FortiOS band string contains | Nautobot frequency |
|---|---|
| `-6G` or `6ghz` | `6GHz` |
| `-5G`, `5ghz`, or `ac` | `5GHz` |
| `-2G`, `2.4`, `802.11g`, `802.11n`, `n,g` | `2.4GHz` |
| `ax` (unsuffixed) | `5GHz` (default) |
| `disabled` or empty | — (radio skipped with warning) |

## Wireless: platform mode

FortiOS `wtp-profile.platform-mode` → Nautobot `WirelessNetworkModeChoices`
(mode lives on the *network* in Nautobot but on the *profile* in FortiOS;
when a VAP is referenced by multiple profiles with different modes,
most-common wins via `Counter`):

| FortiOS | Nautobot |
|---|---|
| `FortiAP-tunnel-mode`, `tunnel-mode` | `Central` |
| `FortiAP-local-mode`, `local-mode` | `Local (Flex)` |
| `wpa-mesh-mode`, `mesh` | `Mesh` |
| `bridge-mode` | `Bridge` |
| unknown | `Central` (default) |

## Name mangling

Most Nautobot firewall-models objects enforce `unique=True` on `name`, so
the same FortiOS object name from two different FortiGates would collide.
This integration mangles names with `<hostname>__<vdom>__<original>` for:

- `AddressObject`
- `AddressObjectGroup`
- `FQDN` (created indirectly when AddressObject type=fqdn)
- `Zone` (not yet synced)
- `Policy`, `NATPolicy`
- `PolicyRule`, `NATPolicyRule`
- `ServiceObjectGroup`
- `WirelessNetwork`
- `RadioProfile`

The original FortiOS name is preserved in the `description` field as
`"<original>: <free-text>"`.

**Exceptions** (no mangling):

- `ServiceObject` — composite natural key `(ip_protocol, port, name)`
  already provides uniqueness without name mangling. Services from
  different FortiGates with the same `(proto, port, name)` collapse to
  one Nautobot row (shared pool).
- AP `Device` (when push synced) — uses serial number as identifier;
  FortiAP serials are globally unique.

## NAT (VIP) synthesis

FortiOS `firewall/vip` entries inline their IPs into the VIP definition
itself — the `extip` and `mappedip` aren't separate AddressObjects. This
integration synthesizes the required AddressObjects on-the-fly:

| FortiOS field | Synthesized Nautobot AddressObject |
|---|---|
| `extip` | `<hostname>__<vdom>__vip_<vipname>_ext` (type=ipaddress) |
| `mappedip[0].range` (single IP) | `<hostname>__<vdom>__vip_<vipname>_mapped` (type=ipaddress) |
| `mappedip[0].range` (range, e.g. `10.0.30.10-10.0.30.20`) | `<hostname>__<vdom>__vip_<vipname>_mapped` (type=iprange) |

When `portforward=enable` on the VIP, ServiceObjects are also synthesized:

| FortiOS field | Synthesized ServiceObject |
|---|---|
| `extport` | `VIP_<vipname>_ext` with `(protocol, extport)` |
| `mappedport` | `VIP_<vipname>_mapped` with `(protocol, mappedport)` |

These records are scoped to the FortiGate the same way other AddressObjects
are (via the mangled-name prefix), so the Nautobot adapter's load() and
the push adapter's diff both treat them as integration-managed objects.
