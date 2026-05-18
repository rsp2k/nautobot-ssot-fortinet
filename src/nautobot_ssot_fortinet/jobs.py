"""Nautobot SSoT Jobs that drive the Fortinet sync.

Phase 2: one Job, ``FortiGateFirewallDataSource``, that pulls firewall
objects (addresses, address groups, services, service groups) from a
FortiGate into ``nautobot-firewall-models``.

The Job is parameterized by an ``ExternalIntegration`` ObjectVar — the
operator picks which FortiGate to sync at run time. Credentials resolve
from the linked SecretsGroup; the actual REST calls happen inside
``load_source_adapter()``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from nautobot.apps.jobs import BooleanVar, Job, ObjectVar, StringVar, register_jobs
from nautobot.dcim.models import DeviceType, Location
from nautobot.extras.models import ExternalIntegration, Role, Status
from nautobot_ssot.jobs.base import DataSource, DataTarget

from nautobot_ssot_fortinet.clients.fortigate import build_client
from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall import (
    FortiGateFirewallAdapter,
)
from nautobot_ssot_fortinet.diffsync.adapters.fortigate_firewall_target import (
    FortiGateFirewallTargetAdapter,
)
from nautobot_ssot_fortinet.diffsync.adapters.fortigate_wireless import (
    FortiGateWirelessAdapter,
)
from nautobot_ssot_fortinet.diffsync.adapters.fortigate_wireless_target import (
    FortiGateWirelessTargetAdapter,
)
from nautobot_ssot_fortinet.diffsync.adapters.nautobot_firewall import (
    NautobotFirewallAdapter,
)
from nautobot_ssot_fortinet.diffsync.adapters.nautobot_wireless import (
    NautobotWirelessAdapter,
)


class FortiGateFirewallDataSource(DataSource):
    """Pull FortiGate firewall config into Nautobot.

    Object types synced: ``firewall/address``, ``firewall/addrgrp``,
    ``firewall.service/custom``, ``firewall.service/group``.

    Set ``delete_records_missing_from_source = True`` to enable
    destructive sync (Nautobot records that no longer exist on the
    FortiGate get deleted). Default is **False** = additive only —
    safer when humans may have hand-added firewall-models records.
    """

    external_integration = ObjectVar(
        model=ExternalIntegration,
        description=(
            "Nautobot ExternalIntegration pointing at the FortiGate. "
            "Its remote_url is the FortiGate REST endpoint; its secrets_group "
            "holds the API token (or username + password fallback)."
        ),
    )
    vdom = StringVar(
        default="root",
        description="FortiOS Virtual Domain to sync from. Defaults to 'root'.",
    )
    delete_records_missing_from_source = BooleanVar(
        default=False,
        description=(
            "If True, delete Nautobot records that no longer exist on the "
            "FortiGate. If False (default), only create/update — leave "
            "orphan records alone."
        ),
    )

    class Meta:
        """Job metadata visible in the SSoT dashboard."""

        name = "FortiGate -> Nautobot (firewall)"
        data_source = "FortiGate"
        description = "Pull FortiGate firewall objects (addresses, services, groups) into nautobot-firewall-models."

    def run(self, *args, **kwargs):  # type: ignore[override]
        """Capture our custom form kwargs as instance attrs, then run base sync.

        ``nautobot_ssot.contrib.DataSource.run()`` only captures the base
        SSoT form vars (``dryrun``, ``memory_profiling``,
        ``parallel_loading``). Our custom ObjectVar / StringVar / BooleanVar
        fields don't auto-populate as instance attrs — without this
        override, ``self.external_integration`` etc. resolve to the
        class-level descriptor objects and crash on attribute access.
        Fixed in v2.9 after the issue surfaced on the first real UI Job run.
        """
        self.external_integration = kwargs["external_integration"]
        self.vdom = kwargs["vdom"]
        self.delete_records_missing_from_source = kwargs["delete_records_missing_from_source"]
        super().run(*args, **kwargs)

    def load_source_adapter(self) -> None:
        """Build the FortiGate client + adapter, load all four object kinds."""
        self.logger.info(f"Connecting to FortiGate via ExternalIntegration {self.external_integration.name!r}...")
        client = build_client(self.external_integration)
        self.source_adapter = FortiGateFirewallAdapter(
            client=client,
            hostname=self.external_integration.name,
            vdom=self.vdom,
            job=self,
            sync=self.sync,
        )
        self.source_adapter.load()
        self.logger.info(
            f"Loaded from FortiGate: "
            f"{len(self.source_adapter.get_all('address_object'))} addresses, "
            f"{len(self.source_adapter.get_all('address_object_group'))} address groups, "
            f"{len(self.source_adapter.get_all('service_object'))} services, "
            f"{len(self.source_adapter.get_all('service_object_group'))} service groups."
        )

    def load_target_adapter(self) -> None:
        """Build the Nautobot adapter (scoped by hostname + vdom), load existing records."""
        self.target_adapter = NautobotFirewallAdapter(
            hostname=self.external_integration.name,
            vdom=self.vdom,
            job=self,
            sync=self.sync,
        )
        self.target_adapter.load()
        self.logger.info(
            f"Loaded from Nautobot (scoped to "
            f"name prefix {self.target_adapter.name_prefix!r}): "
            f"{len(self.target_adapter.get_all('address_object'))} addresses, "
            f"{len(self.target_adapter.get_all('address_object_group'))} address groups, "
            f"{len(self.target_adapter.get_all('service_object'))} services, "
            f"{len(self.target_adapter.get_all('service_object_group'))} service groups."
        )

    def execute_sync(self) -> None:
        """Run the sync — honoring the additive-only flag."""
        if not self.delete_records_missing_from_source:
            # Strip "delete" actions from the diff before applying.
            for top in self.target_adapter.top_level:
                self.diff.remove_unprocessed_children(top, "-")
            self.logger.info("Additive-only mode: any Nautobot records absent from the FortiGate were NOT deleted.")
        super().execute_sync()


class FortiGateWirelessDataSource(DataSource):
    """Pull FortiGate wireless config into Nautobot.

    Object types synced:
      - ``wireless-controller/vap``  → ``nautobot.wireless.WirelessNetwork``
      - ``wireless-controller/wtp-profile`` (radios fanned out per band)
                                     → ``nautobot.wireless.RadioProfile``
      - ``wireless-controller/wtp`` (optional, opt-in)
                                     → ``nautobot.dcim.Device`` (role=AP)

    The three ``ap_*`` ObjectVars are optional. If any is unset, the Job
    runs in "no AP Device sync" mode and only syncs WirelessNetwork +
    RadioProfile. This is the right mode for all-in-one devices like the
    FortiWiFi-61E that have built-in radios but no separate managed APs.
    """

    external_integration = ObjectVar(
        model=ExternalIntegration,
        description=(
            "Nautobot ExternalIntegration pointing at the FortiGate. "
            "Same kind used by the firewall sync — can be the same record."
        ),
    )
    vdom = StringVar(
        default="root",
        description="FortiOS Virtual Domain to sync from.",
    )
    delete_records_missing_from_source = BooleanVar(
        default=False,
        description=(
            "If True, delete Nautobot wireless records that no longer exist on "
            "the FortiGate. Default False = additive only."
        ),
    )
    ap_device_type = ObjectVar(
        model=DeviceType,
        required=False,
        description=(
            "Optional — DeviceType to assign to auto-created FortiAP Devices. "
            "Leave unset to skip AP Device sync (recommended for FortiWiFi "
            "all-in-one units like the FWF-61E)."
        ),
    )
    ap_role = ObjectVar(
        model=Role,
        required=False,
        description="Optional — Role to assign to auto-created FortiAP Devices.",
    )
    ap_location = ObjectVar(
        model=Location,
        required=False,
        description="Optional — Location to assign to auto-created FortiAP Devices.",
    )

    class Meta:
        """Job metadata visible in the SSoT dashboard."""

        name = "FortiGate -> Nautobot (wireless)"
        data_source = "FortiGate"
        description = (
            "Pull FortiGate wireless objects (SSIDs, radio profiles, optionally "
            "managed FortiAPs) into Nautobot core wireless models."
        )

    def run(self, *args, **kwargs):  # type: ignore[override]
        """Capture form kwargs as instance attrs, then run base sync.

        See FortiGateFirewallDataSource.run() for the rationale — same
        v2.9 fix applied here, plus the three optional AP ObjectVars.
        """
        self.external_integration = kwargs["external_integration"]
        self.vdom = kwargs["vdom"]
        self.delete_records_missing_from_source = kwargs["delete_records_missing_from_source"]
        self.ap_device_type = kwargs.get("ap_device_type")
        self.ap_role = kwargs.get("ap_role")
        self.ap_location = kwargs.get("ap_location")
        super().run(*args, **kwargs)

    @property
    def sync_access_points(self) -> bool:
        """True only when all three AP ObjectVars are populated."""
        return bool(self.ap_device_type and self.ap_role and self.ap_location)

    def _ap_kwargs(self) -> dict:
        return {
            "sync_access_points": self.sync_access_points,
            "ap_device_type_model": self.ap_device_type.model if self.ap_device_type else "",
            "ap_role_name": self.ap_role.name if self.ap_role else "",
            "ap_location_name": self.ap_location.name if self.ap_location else "",
        }

    def load_source_adapter(self) -> None:
        """Build the FortiGate wireless adapter, load vap + wtp-profile (+ optional wtp).

        Same context-manager rationale as the firewall Job — one admin
        session across all queries, especially important for user/pass auth.
        """
        self.logger.info(f"Connecting to FortiGate via ExternalIntegration {self.external_integration.name!r}...")
        with build_client(self.external_integration) as client:
            self.source_adapter = FortiGateWirelessAdapter(
                client=client,
                hostname=self.external_integration.name,
                vdom=self.vdom,
                job=self,
                sync=self.sync,
                **self._ap_kwargs(),
            )
            self.source_adapter.load()
        self.logger.info(
            f"Loaded from FortiGate: "
            f"{len(self.source_adapter.get_all('wireless_network'))} WirelessNetworks, "
            f"{len(self.source_adapter.get_all('radio_profile'))} RadioProfiles, "
            f"{len(self.source_adapter.get_all('access_point'))} APs."
        )

    def load_target_adapter(self) -> None:
        """Build the Nautobot wireless adapter (name-prefix scoped), load existing records."""
        self.target_adapter = NautobotWirelessAdapter(
            hostname=self.external_integration.name,
            vdom=self.vdom,
            job=self,
            sync=self.sync,
            **self._ap_kwargs(),
        )
        self.target_adapter.load()
        self.logger.info(
            f"Loaded from Nautobot (scoped to "
            f"name prefix {self.target_adapter.name_prefix!r}): "
            f"{len(self.target_adapter.get_all('wireless_network'))} WirelessNetworks, "
            f"{len(self.target_adapter.get_all('radio_profile'))} RadioProfiles, "
            f"{len(self.target_adapter.get_all('access_point'))} APs."
        )

    def execute_sync(self) -> None:
        """Run the sync — strip deletes from the diff if additive-only flag is set."""
        if not self.delete_records_missing_from_source:
            for top in self.target_adapter.top_level:
                self.diff.remove_unprocessed_children(top, "-")
            self.logger.info("Additive-only mode: any Nautobot records absent from the FortiGate were NOT deleted.")
        super().execute_sync()


class FortiGateLiveStatus(Job):
    """Pull live runtime state from a FortiGate — connected wifi clients, DHCP leases, ARP table.

    Unlike the SSoT sync Jobs, this one doesn't persist anything to Nautobot
    models. It queries FortiOS ``monitor/*`` endpoints (which expose
    real-time observed state, not configuration intent) and renders a
    point-in-time table to the Job's log output, plus attaches a JSON
    snapshot file for download.

    Use this for:
    - "Who's connected to my wifi right now?" troubleshooting
    - Capturing a moment-in-time inventory before/after network changes
    - Verifying expected devices are still associated after a config push

    Joins three FortiOS endpoints by MAC address:
      - ``monitor/wifi/client``  → SSID, signal, data rate, association time
      - ``monitor/system/dhcp``  → hostname (via VCI), lease IP, expiry
      - ``monitor/network/arp``  → backup IP/MAC binding for non-DHCP clients
    """

    external_integration = ObjectVar(
        model=ExternalIntegration,
        description="FortiGate to query. Same ExternalIntegration as the sync Jobs.",
    )

    class Meta:
        """Job metadata."""

        name = "FortiGate Live Status"
        description = "Snapshot live state from a FortiGate (wifi clients, DHCP, ARP)."

    def run(self, external_integration):  # type: ignore[override]
        """Job entry point — invoked by the SSoT/Job runner."""
        with build_client(external_integration) as fgt:
            wifi_clients = _safe_get(fgt, "api/v2/monitor/wifi/client")
            dhcp_leases = _safe_get(fgt, "api/v2/monitor/system/dhcp")
            arp_entries = _safe_get(fgt, "api/v2/monitor/network/arp")
            managed_aps = _safe_get(fgt, "api/v2/monitor/wifi/managed_ap")

        # Build a MAC → enrichment dict from DHCP + ARP. Keys lowercased so
        # we can lookup case-insensitively from wifi/client (FortiOS is
        # inconsistent about MAC casing across endpoints).
        enrichment: dict[str, dict] = {}
        for lease in dhcp_leases:
            mac = (lease.get("mac") or "").lower()
            if mac:
                enrichment.setdefault(mac, {}).update(
                    dhcp_ip=lease.get("ip"),
                    dhcp_hostname=lease.get("hostname"),
                    dhcp_interface=lease.get("interface"),
                    dhcp_vci=lease.get("vci"),
                )
        for arp in arp_entries:
            mac = (arp.get("mac") or "").lower()
            if mac:
                enrichment.setdefault(mac, {}).update(
                    arp_ip=arp.get("ip"),
                    arp_interface=arp.get("interface"),
                )

        # Render the wifi client table to the Job log.
        self.logger.info(
            f"=== FortiGate live status — {external_integration.name} "
            f"@ {datetime.now(timezone.utc).isoformat(timespec='seconds')} ==="
        )
        self.logger.info(
            f"Wifi clients: {len(wifi_clients)}  |  "
            f"DHCP leases: {len(dhcp_leases)}  |  "
            f"ARP entries: {len(arp_entries)}  |  "
            f"Managed APs: {len(managed_aps)}"
        )

        if not wifi_clients:
            self.logger.info("No wifi clients currently associated.")
        else:
            self.logger.info("")
            self.logger.info("Wifi clients (joined with DHCP + ARP by MAC):")
            self.logger.info(_render_table(wifi_clients, enrichment))

        # Build a structured snapshot for the downloadable JSON file.
        snapshot = {
            "fortigate": external_integration.name,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "wifi_clients": len(wifi_clients),
                "dhcp_leases": len(dhcp_leases),
                "arp_entries": len(arp_entries),
                "managed_aps": len(managed_aps),
            },
            "wifi_clients": [_enrich_client(c, enrichment) for c in wifi_clients],
            "dhcp_leases": dhcp_leases,
            "arp_entries": arp_entries,
            "managed_aps": managed_aps,
        }
        filename = (
            f"fortigate-live-status-{external_integration.name}-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        self.create_file(filename, json.dumps(snapshot, indent=2, default=str))
        self.logger.info("")
        self.logger.info(f"Full snapshot attached to this Job result as {filename!r}.")


def _safe_get(fgt, url: str) -> list[dict]:
    """Wrap ``FortiGate.get_results`` so a single failed endpoint doesn't kill the Job."""
    try:
        data = fgt.fortigate.get_results(url)
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 — monitor endpoints can be flaky; per-EP fail-soft
        return []


def _render_table(wifi_clients: list[dict], enrichment: dict[str, dict]) -> str:
    """Render a single-line-per-client text table."""
    header = f"  {'MAC':<18} {'IP':<16} {'Hostname':<22} {'SSID':<12} {'Rate(Mb)':>9} {'Auth':<8}"
    rule = "  " + "-" * 86
    lines = [header, rule]
    for c in wifi_clients:
        mac = (c.get("mac") or "").lower()
        ip = c.get("ip", "?")
        enrich = enrichment.get(mac, {})
        hostname = enrich.get("dhcp_hostname") or enrich.get("dhcp_vci") or "(unknown)"
        ssid = c.get("ssid", "?")
        # FortiOS gives data_rate_bps in bits — convert to Mb for display.
        rate_mb = (c.get("data_rate_bps") or 0) // 1_000_000
        # 'health' is a structured dict in FortiOS 7.x ({"signal": "good", ...});
        # 'authentication' is a single string ("pass"/"fail") which is more useful.
        auth = c.get("authentication", "?")
        lines.append(f"  {mac:<18} {ip:<16} {hostname[:22]:<22} {ssid[:12]:<12} {rate_mb:>9} {auth:<8}")
    return "\n".join(lines)


def _enrich_client(client: dict, enrichment: dict[str, dict]) -> dict:
    """Merge a wifi/client record with its dhcp/arp enrichment."""
    mac = (client.get("mac") or "").lower()
    out = dict(client)
    out["_enrichment"] = enrichment.get(mac, {})
    return out


class FortiGateFirewallDataTarget(DataTarget):
    """Push Nautobot AddressObjects (ipmask type only) to FortiGate.

    This is the **inverse** of ``FortiGateFirewallDataSource``: Nautobot is
    the source of truth, FortiGate is the target. Used after an operator
    edits firewall objects in Nautobot's UI and wants the FortiGate to
    reflect those changes.

    Scope (v0): AddressObjects of type ``ipmask`` only. Other types
    (fqdn, iprange, ipaddress) and other object kinds (groups, services,
    policies, NAT) will be added once the bidirectional pattern is
    validated. The push Job's first run against a freshly-pulled Nautobot
    state should diff to **zero** — that's the round-trip symmetry proof.
    """

    external_integration = ObjectVar(
        model=ExternalIntegration,
        description="FortiGate to push to. Must already be synced via the pull Job first.",
    )
    vdom = StringVar(
        default="root",
        description="FortiOS VDOM scope. Must match what the pull Job used.",
    )
    delete_records_missing_from_source = BooleanVar(
        default=False,
        description=(
            "If True, delete FortiGate records that no longer exist in Nautobot. "
            "DANGEROUS — could remove FortiGate config you didn't intend to delete. "
            "Default False = additive/update only."
        ),
    )

    class Meta:
        """Job metadata."""

        name = "Nautobot -> FortiGate (firewall)"
        data_source = "Nautobot"
        data_target = "FortiGate"
        description = (
            "Push Nautobot firewall objects (AddressObject, AddressObjectGroup, "
            "ServiceObject, ServiceObjectGroup, PolicyRule, NATPolicyRule) to a "
            "FortiGate via REST. Full CRUD across every model — see release "
            "notes for v2.5–v2.7 for the live-validated capability matrix."
        )

    def run(self, *args, **kwargs):  # type: ignore[override]
        """Capture form kwargs as instance attrs, then run base sync (v2.9 fix)."""
        self.external_integration = kwargs["external_integration"]
        self.vdom = kwargs["vdom"]
        self.delete_records_missing_from_source = kwargs["delete_records_missing_from_source"]
        super().run(*args, **kwargs)

    def load_source_adapter(self) -> None:
        """Load the Nautobot-side adapter (read-only) scoped to this FortiGate's prefix."""
        self.source_adapter = NautobotFirewallAdapter(
            hostname=self.external_integration.name,
            vdom=self.vdom,
            job=self,
            sync=self.sync,
        )
        self.source_adapter.load()
        self.logger.info(
            f"Loaded from Nautobot (scoped to {self.source_adapter.name_prefix!r}): "
            f"{len(self.source_adapter.get_all('address_object'))} AddressObjects"
        )

    def load_target_adapter(self) -> None:
        """Load CURRENT FortiGate state into the write-enabled target adapter."""
        self.logger.info(f"Connecting to FortiGate via ExternalIntegration {self.external_integration.name!r}...")
        with build_client(self.external_integration) as client:
            self.target_adapter = FortiGateFirewallTargetAdapter(
                client=client,
                hostname=self.external_integration.name,
                vdom=self.vdom,
                job=self,
                sync=self.sync,
            )
            self.target_adapter.load()
        self.logger.info(
            f"Loaded current FortiGate state: {len(self.target_adapter.get_all('address_object'))} AddressObjects"
        )

    def execute_sync(self) -> None:
        """Apply the diff — strip deletes unless explicitly enabled.

        Uses the SAME client context manager pattern as the pull side so
        we have one admin session for all create/update/delete REST calls.
        """
        if not self.delete_records_missing_from_source:
            for top in self.target_adapter.top_level:
                self.diff.remove_unprocessed_children(top, "-")
            self.logger.info("Additive-only mode: any FortiGate records absent from Nautobot were NOT deleted.")

        # v2.2+: expose the source adapter on the target so model.create()
        # methods can do sibling aggregation (mirrors wireless Job).
        self.target_adapter.source_adapter = self.source_adapter

        # Re-open the client for the sync phase (the load() phase closed it).
        # The target_adapter's model classes call self.adapter.client — so
        # we re-attach a fresh logged-in client before sync_from runs.
        with build_client(self.external_integration) as client:
            self.target_adapter.client = client
            super().execute_sync()


class FortiGateWirelessDataTarget(DataTarget):
    """Push Nautobot wireless config to a FortiGate.

    Scope (v2.0):

    - ``WirelessNetwork`` (VAP) — full create/update/delete via
      ``cmdb/wireless-controller/vap``
    - ``RadioProfile`` — **update only** via partial wtp-profile updates.
      Parent wtp-profile must exist on the device; create/delete of a
      single radio is not supported.
    - Access Points (Devices) — push is a no-op in this version.
    """

    external_integration = ObjectVar(
        model=ExternalIntegration,
        description="FortiGate to push to. Must already be synced via the pull Job first.",
    )
    vdom = StringVar(
        default="root",
        description="FortiOS VDOM scope. Must match what the pull Job used.",
    )
    delete_records_missing_from_source = BooleanVar(
        default=False,
        description=(
            "If True, delete FortiGate wireless records that no longer exist in "
            "Nautobot. DANGEROUS — could remove wireless config you didn't intend "
            "to delete. Default False = additive/update only."
        ),
    )

    class Meta:
        """Job metadata."""

        name = "Nautobot -> FortiGate (wireless)"
        data_source = "Nautobot"
        data_target = "FortiGate"
        description = "Push Nautobot WirelessNetworks (VAPs) + RadioProfile updates to a FortiGate."

    def run(self, *args, **kwargs):  # type: ignore[override]
        """Capture form kwargs as instance attrs, then run base sync (v2.9 fix)."""
        self.external_integration = kwargs["external_integration"]
        self.vdom = kwargs["vdom"]
        self.delete_records_missing_from_source = kwargs["delete_records_missing_from_source"]
        super().run(*args, **kwargs)

    def load_source_adapter(self) -> None:
        """Load Nautobot wireless state (read-only) scoped to this FortiGate's prefix."""
        self.source_adapter = NautobotWirelessAdapter(
            hostname=self.external_integration.name,
            vdom=self.vdom,
            job=self,
            sync=self.sync,
        )
        self.source_adapter.load()
        self.logger.info(
            f"Loaded from Nautobot: "
            f"{len(self.source_adapter.get_all('wireless_network'))} WirelessNetworks, "
            f"{len(self.source_adapter.get_all('radio_profile'))} RadioProfiles"
        )

    def load_target_adapter(self) -> None:
        """Load current FortiGate wireless state into the write-enabled target adapter."""
        self.logger.info(f"Connecting to FortiGate via ExternalIntegration {self.external_integration.name!r}...")
        with build_client(self.external_integration) as client:
            self.target_adapter = FortiGateWirelessTargetAdapter(
                client=client,
                hostname=self.external_integration.name,
                vdom=self.vdom,
                job=self,
                sync=self.sync,
            )
            self.target_adapter.load()

    def execute_sync(self) -> None:
        """Apply the diff with a re-opened client; strip deletes unless explicitly enabled."""
        if not self.delete_records_missing_from_source:
            for top in self.target_adapter.top_level:
                self.diff.remove_unprocessed_children(top, "-")
            self.logger.info("Additive-only mode: any FortiGate records absent from Nautobot were NOT deleted.")
        # v2.2+: expose the source adapter on the target so model.create()
        # methods can do sibling aggregation (used by wtp-profile create).
        self.target_adapter.source_adapter = self.source_adapter
        with build_client(self.external_integration) as client:
            self.target_adapter.client = client
            super().execute_sync()


class FortiGateDevicesDataSource(DataSource):
    """Pull the FortiGate as a Nautobot Device with its interfaces (v3.0+).

    Read-only sync. Creates / updates:

    - One ``dcim.Device`` for the FortiGate itself, scoped by the
      operator-supplied DeviceType, Role, Location, and Status
    - One ``dcim.Interface`` per FortiOS physical / hard-switch / switch /
      aggregate interface in the selected VDOM
    - One ``ipam.IPAddress`` per assigned interface IP, attached via
      ``interface.ip_addresses``

    Skipped interface types (v3.0):
      - ``vap-switch`` — already covered by the wireless sync
      - ``vlan`` — mostly auto-created quarantine artifacts; defer
      - ``tunnel`` — VPN-specific, defer to VPN-focused release

    No push direction in v3.0 — wrong-IP on a FortiGate interface
    can disconnect the appliance, so the read-write equivalent will
    require explicit operator opt-in plus pre-validation. Tracked
    for v3.1+.
    """

    external_integration = ObjectVar(
        model=ExternalIntegration,
        description=(
            "FortiGate to sync from. Must have a SecretsGroup with the "
            "REST API token (or username + password fallback)."
        ),
    )
    vdom = StringVar(
        default="root",
        description="FortiOS Virtual Domain to sync interfaces from. Defaults to 'root'.",
    )
    device_type = ObjectVar(
        model=DeviceType,
        description=("Nautobot DeviceType for the FortiGate appliance (e.g. 'FortiWiFi-61E'). Must already exist."),
    )
    role = ObjectVar(
        model=Role,
        description="Nautobot Role for the synced Device (e.g. 'Firewall'). Must already exist.",
    )
    location = ObjectVar(
        model=Location,
        description="Nautobot Location for the synced Device. Must already exist.",
    )
    status = ObjectVar(
        model=Status,
        description="Nautobot Status for the synced Device (typically 'Active').",
    )
    delete_records_missing_from_source = BooleanVar(
        default=False,
        description=(
            "If True, delete Nautobot Interface records that no longer exist "
            "on the FortiGate. If False (default), only create/update — leave "
            "orphan records alone."
        ),
    )

    class Meta:
        """Job metadata."""

        name = "FortiGate -> Nautobot (device + interfaces)"
        data_source = "FortiGate"
        description = (
            "Pull the FortiGate as a Nautobot Device with its physical, "
            "hard-switch, switch, and aggregate interfaces (including IP "
            "assignments). Read-only — push direction deferred to v3.1+."
        )

    def run(self, *args, **kwargs):  # type: ignore[override]
        """Capture form kwargs as instance attrs, then run base sync (v2.9 pattern)."""
        self.external_integration = kwargs["external_integration"]
        self.vdom = kwargs["vdom"]
        self.device_type = kwargs["device_type"]
        self.role = kwargs["role"]
        self.location = kwargs["location"]
        self.status = kwargs["status"]
        self.delete_records_missing_from_source = kwargs["delete_records_missing_from_source"]
        super().run(*args, **kwargs)

    def load_source_adapter(self) -> None:
        """Build the FortiGate adapter, load device + interface state."""
        from nautobot_ssot_fortinet.diffsync.adapters.fortigate_devices import (
            FortiGateDevicesAdapter,
        )

        self.logger.info(f"Connecting to FortiGate via ExternalIntegration {self.external_integration.name!r}...")
        with build_client(self.external_integration) as client:
            self.source_adapter = FortiGateDevicesAdapter(
                client=client,
                hostname=self.external_integration.name,
                vdom=self.vdom,
                device_type_model=self.device_type.model,
                role_name=self.role.name,
                location_name=self.location.name,
                status_name=self.status.name,
                job=self,
                sync=self.sync,
            )
            self.source_adapter.load()

    def load_target_adapter(self) -> None:
        """Read existing Nautobot Device + Interface state for the target FortiGate."""
        from nautobot_ssot_fortinet.diffsync.adapters.nautobot_devices import (
            NautobotDevicesAdapter,
        )

        self.target_adapter = NautobotDevicesAdapter(
            hostname=self.external_integration.name,
            vdom=self.vdom,
            device_type_model=self.device_type.model,
            role_name=self.role.name,
            location_name=self.location.name,
            status_name=self.status.name,
            job=self,
            sync=self.sync,
        )
        self.target_adapter.load()


jobs = [
    FortiGateFirewallDataSource,
    FortiGateWirelessDataSource,
    FortiGateLiveStatus,
    FortiGateFirewallDataTarget,
    FortiGateWirelessDataTarget,
    FortiGateDevicesDataSource,
]
register_jobs(*jobs)
