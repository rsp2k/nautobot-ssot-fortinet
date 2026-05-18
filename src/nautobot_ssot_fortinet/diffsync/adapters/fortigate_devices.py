"""FortiGate-side DiffSync adapter for Device + Interface + Route sync (v3.0 + v3.1).

Pulls the FortiGate's identity (serial, hostname) from ``system.global``,
its interfaces from ``system.interface`` (v3.0 + v3.1 VLAN support), and
its static routes from ``router.static`` (new in v3.1).

Form-var dependencies (set on the adapter instance by the Job):

- ``device_type_model``, ``role_name``, ``location_name``, ``status_name``
  — operator-specified Nautobot scoping references for the Device record
  to be created. Pulled from the Job's ObjectVars at sync time.
- ``include_static_routes`` (v3.1) — when False, skip the router.static
  pull. Lets operators run the Job at "just devices + interfaces" scope.
"""

from __future__ import annotations

from diffsync import Adapter

from nautobot_ssot_fortinet.diffsync.models.devices import (
    FortiGateDevice,
    FortiGateInterface,
    FortiGateStaticRoute,
)
from nautobot_ssot_fortinet.utils.fortios import (
    fortios_interface_ip_to_cidr,
    fortios_interface_type_to_nautobot,
    fortios_route_destination_cidr,
    is_internal_fortios_interface,
)


class FortiGateDevicesAdapter(Adapter):
    """Load FortiGate Device + Interface (+ optional Route) state from the live appliance."""

    fortigate_device = FortiGateDevice
    fortigate_interface = FortiGateInterface
    fortigate_static_route = FortiGateStaticRoute

    top_level = ("fortigate_device", "fortigate_interface", "fortigate_static_route")

    def __init__(
        self,
        *,
        client,
        hostname: str,
        vdom: str = "root",
        device_type_model: str = "",
        role_name: str = "",
        location_name: str = "",
        status_name: str = "Active",
        include_static_routes: bool = True,
        job=None,
        sync=None,
    ):
        super().__init__()
        self.client = client
        self.hostname = hostname
        self.vdom = vdom
        self.device_type_model = device_type_model
        self.role_name = role_name
        self.location_name = location_name
        self.status_name = status_name
        self.include_static_routes = include_static_routes
        self.job = job
        self.sync = sync

    def load(self) -> None:
        """Pull system.global, system.interface, and (optionally) router.static."""
        self._load_device()
        self._load_interfaces()
        if self.include_static_routes:
            self._load_static_routes()

    def _load_device(self) -> None:
        """Build a FortiGateDevice record from system.global.

        ``system.global`` returns the FortiOS serial as part of an envelope
        we already see in every response (``serial`` key on the wrapping
        request metadata). Easiest path: peek at the response headers via
        the connector's last response. Fallback: parse from any cmdb get
        response, all of which carry the serial.
        """
        # The fortigate-api client wraps responses with a ``serial`` field
        # at the top level. The cleanest way to grab it without an extra
        # round-trip is to make any small cmdb call and read it from the
        # raw response. system.global is appropriate but its connector
        # name varies — use system.interface (which we'll need anyway).
        serial = self._get_fortios_serial()

        self.add(
            self.fortigate_device(
                name=self.hostname,
                serial=serial,
                device_type_model=self.device_type_model,
                role_name=self.role_name,
                location_name=self.location_name,
                status_name=self.status_name,
                vdom=self.vdom,
            )
        )

    def _get_fortios_serial(self) -> str:
        """Extract the FortiOS serial from any cmdb response envelope.

        Every fortigate-api response includes ``serial`` at the top level.
        We grab it from the raw HTTP response on the next call we make.
        """
        try:
            # The connector strips the envelope by default; we need raw
            # access via the underlying session. Call fortigate.get directly
            # with a minimal-payload URL so the response is small.
            raw = self.client.fortigate.get_result("/api/v2/cmdb/system/interface?count=1")
            # Some FortiOS versions wrap differently. Just return empty
            # if we can't extract — operator can set via the form var
            # if needed later.
            if isinstance(raw, dict):
                return raw.get("serial", "") or ""
            return ""
        except Exception as e:  # noqa: BLE001
            if self.job:
                self.job.logger.warning(f"Could not extract FortiGate serial: {e}")
            return ""

    def _load_interfaces(self) -> None:
        """Load system.interface and create FortiGateInterface records.

        Skip policy (v3.1):
            1. Type-based skip — types that map to None via
               :func:`fortios_interface_type_to_nautobot` (vap-switch, tunnel)
            2. Name-based skip — internal FortiOS artifacts via
               :func:`is_internal_fortios_interface` (wqtn.*, vap.*, ssl.*, naf.*)
            3. VDOM mismatch — skip interfaces not in operator's selected VDOM

        VLAN sub-interfaces (``type=vlan``) — surfaced in v3.1+ with their
        ``interface`` field (parent name) and ``vlanid`` (802.1Q VLAN ID).
        Both stored on the DiffSync model so the Nautobot side can resolve
        the parent FK and create the VLAN record.
        """
        raw_ifs = self.client.cmdb.system.interface.get()
        skipped = {"by_type": {}, "by_name": 0, "by_vdom_mismatch": 0}
        loaded = 0
        vlans_loaded = 0

        for raw in raw_ifs:
            ftype = raw.get("type", "physical")
            nautobot_type = fortios_interface_type_to_nautobot(ftype)
            if nautobot_type is None:
                skipped["by_type"][ftype] = skipped["by_type"].get(ftype, 0) + 1
                continue

            name = raw.get("name", "")
            if not name:
                continue

            # v3.1: name-based skip for FortiOS-internal artifacts
            if is_internal_fortios_interface(name):
                skipped["by_name"] += 1
                continue

            # FortiGate interfaces have a vdom field; only sync interfaces
            # in the operator's selected vdom (matches firewall sync behavior).
            if_vdom = raw.get("vdom", "root")
            if if_vdom != self.vdom:
                skipped["by_vdom_mismatch"] += 1
                continue

            cidrs = self._parse_interface_ips(raw)

            # v3.1: extract VLAN sub-interface attrs. FortiOS ``vlanid``
            # is set on type=vlan AND on type=physical with VLAN tagging.
            # FortiOS ``interface`` field names the parent for sub-interfaces.
            vlan_id = raw.get("vlanid") if isinstance(raw.get("vlanid"), int) and raw.get("vlanid") > 0 else None
            parent_name = raw.get("interface", "") or ""
            vlan_mode = "tagged" if ftype == "vlan" else ""
            if ftype == "vlan":
                vlans_loaded += 1

            self.add(
                self.fortigate_interface(
                    device_name=self.hostname,
                    name=name,
                    type=nautobot_type,
                    enabled=raw.get("status", "up") == "up",
                    mtu=raw.get("mtu") if isinstance(raw.get("mtu"), int) else None,
                    description=raw.get("description", "") or "",
                    vdom=if_vdom,
                    cidrs=cidrs,
                    parent_interface_name=parent_name,
                    vlan_id=vlan_id,
                    vlan_mode=vlan_mode,
                )
            )
            loaded += 1

        if self.job:
            skip_summary = ", ".join(f"{t}:{n}" for t, n in skipped["by_type"].items())
            self.job.logger.info(
                f"Loaded {loaded} interfaces ({vlans_loaded} VLAN sub-interfaces) "
                f"from FortiGate {self.hostname!r}. "
                f"Skipped by type ({skip_summary}), "
                f"by name (internal artifacts): {skipped['by_name']}, "
                f"by vdom mismatch: {skipped['by_vdom_mismatch']}."
            )

    def _load_static_routes(self) -> None:
        """Load router.static entries and emit FortiGateStaticRoute records.

        FortiOS exposes routes at ``cmdb/router/static``. Each entry has a
        ``seq-num`` (the route's primary key per device/vdom) plus the
        usual dst/gateway/device/distance/priority/comment/blackhole fields.

        Routes that use the named-address-object form (``dstaddr`` instead
        of ``dst``) are skipped with a warning — see
        :func:`fortios_route_destination_cidr` for why.
        """
        try:
            raw_routes = self.client.cmdb.router.static.get()
        except Exception as e:  # noqa: BLE001 — endpoint not present on all FortiOS versions
            if self.job:
                self.job.logger.warning(f"Could not pull router.static from FortiGate: {e}")
            return

        loaded = 0
        skipped_named = 0
        skipped_vdom = 0
        for raw in raw_routes:
            if raw.get("vdom", "root") != self.vdom:
                skipped_vdom += 1
                continue

            destination = fortios_route_destination_cidr(raw)
            if destination is None:
                # Either named-address-object form or malformed dst
                if raw.get("dstaddr"):
                    skipped_named += 1
                    if self.job:
                        names = [d.get("name", "?") for d in raw.get("dstaddr", []) if isinstance(d, dict)]
                        self.job.logger.warning(
                            f"Skipping route seq={raw.get('seq-num')}: uses named-address-object form "
                            f"(dstaddr={names!r}) — v3.1 only supports literal dst CIDRs"
                        )
                continue

            seq_num = raw.get("seq-num")
            if seq_num is None:
                continue

            # FortiOS distinguishes blackhole via the ``blackhole`` field.
            # IMPORTANT: FortiOS returns ``"disable"`` (string) NOT ``False`` —
            # pre-v3.2.2 we wrapped with bool() which made every route look
            # like a blackhole (``bool("disable") == True``). Match the
            # exact "enable" value to catch real blackholes, and accept
            # ``True`` for forward-compat with any FortiOS version that
            # might switch to bools. Gateway is "0.0.0.0" for blackhole;
            # normalize to empty so the diff sees consistent shape.
            bh_raw = raw.get("blackhole")
            blackhole = bh_raw == "enable" or bh_raw is True
            gateway = raw.get("gateway", "") or ""
            if blackhole or gateway == "0.0.0.0":
                gateway = ""

            # FortiOS calls the egress interface ``device``.
            interface_name = raw.get("device", "") or ""

            self.add(
                self.fortigate_static_route(
                    device_name=self.hostname,
                    vdom=self.vdom,
                    seq_num=int(seq_num),
                    destination=destination,
                    gateway=gateway,
                    interface_name=interface_name,
                    distance=int(raw.get("distance", 10)) if raw.get("distance") else 10,
                    priority=int(raw.get("priority", 0)) if raw.get("priority") else 0,
                    blackhole=blackhole,
                    comment=raw.get("comment", "") or "",
                )
            )
            loaded += 1

        if self.job:
            self.job.logger.info(
                f"Loaded {loaded} static routes from FortiGate {self.hostname!r}. "
                f"Skipped {skipped_named} routes using named-address-object form, "
                f"{skipped_vdom} routes in other vdoms."
            )

    def _parse_interface_ips(self, raw: dict) -> list[str]:
        """Extract CIDR strings from a FortiOS interface's ``ip``/``secondary-IP`` fields.

        FortiOS stores interface IPs as ``"1.2.3.4 255.255.255.0"`` (the
        same dotted-mask form as firewall addresses). Skip unassigned
        ``"0.0.0.0 0.0.0.0"`` entries.
        """
        cidrs = []
        primary = raw.get("ip", "")
        if primary and primary != "0.0.0.0 0.0.0.0":
            try:
                cidrs.append(fortios_interface_ip_to_cidr(primary))
            except (ValueError, Exception):
                pass

        for sec in raw.get("secondaryip", []) or []:
            sec_ip = sec.get("ip", "") if isinstance(sec, dict) else ""
            if sec_ip and sec_ip != "0.0.0.0 0.0.0.0":
                try:
                    cidrs.append(fortios_interface_ip_to_cidr(sec_ip))
                except (ValueError, Exception):
                    pass

        return cidrs
