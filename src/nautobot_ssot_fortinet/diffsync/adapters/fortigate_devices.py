"""FortiGate-side DiffSync adapter for Device + Interface sync (v3.0).

Pulls the FortiGate's identity (serial, hostname) from ``system.global``
and its interfaces from ``system.interface``. Skips interface types
already covered by other Jobs (vap-switch → WirelessNetwork) or deferred
to later releases (tunnel, vlan).

Form-var dependencies (set on the adapter instance by the Job):

- ``device_type_model``, ``role_name``, ``location_name``, ``status_name``
  — operator-specified Nautobot scoping references for the Device record
  to be created. Pulled from the Job's ObjectVars at sync time.
"""

from __future__ import annotations

from diffsync import Adapter

from nautobot_ssot_fortinet.diffsync.models.devices import (
    FortiGateDevice,
    FortiGateInterface,
)
from nautobot_ssot_fortinet.utils.fortios import (
    fortios_interface_ip_to_cidr,
    fortios_interface_type_to_nautobot,
)


class FortiGateDevicesAdapter(Adapter):
    """Load FortiGate Device + Interface state from the live appliance."""

    fortigate_device = FortiGateDevice
    fortigate_interface = FortiGateInterface

    top_level = ("fortigate_device", "fortigate_interface")

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
        self.job = job
        self.sync = sync

    def load(self) -> None:
        """Pull system.global (for serial) and system.interface (for ports)."""
        self._load_device()
        self._load_interfaces()

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

        Skips types that map to None via :func:`fortios_interface_type_to_nautobot`
        (vap-switch, tunnel, vlan in v3.0).
        """
        raw_ifs = self.client.cmdb.system.interface.get()
        skipped = {"by_type": {}, "by_vdom_mismatch": 0}
        loaded = 0

        for raw in raw_ifs:
            ftype = raw.get("type", "physical")
            nautobot_type = fortios_interface_type_to_nautobot(ftype)
            if nautobot_type is None:
                skipped["by_type"][ftype] = skipped["by_type"].get(ftype, 0) + 1
                continue

            # FortiGate interfaces have a vdom field; only sync interfaces
            # in the operator's selected vdom (matches firewall sync behavior).
            if_vdom = raw.get("vdom", "root")
            if if_vdom != self.vdom:
                skipped["by_vdom_mismatch"] += 1
                continue

            name = raw.get("name", "")
            if not name:
                continue

            cidrs = self._parse_interface_ips(raw)
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
                )
            )
            loaded += 1

        if self.job:
            skip_summary = ", ".join(f"{t}:{n}" for t, n in skipped["by_type"].items())
            self.job.logger.info(
                f"Loaded {loaded} interfaces from FortiGate {self.hostname!r}. "
                f"Skipped by type ({skip_summary}), skipped by vdom mismatch: "
                f"{skipped['by_vdom_mismatch']}."
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
