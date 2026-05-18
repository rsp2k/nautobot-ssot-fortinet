"""Django models added by the Fortinet SSoT app (v3.1+).

Currently houses ``FortinetStaticRoute`` — a first-class representation of a
FortiOS ``router.static`` entry. Nautobot 3.x has no built-in Route model, so
rather than overloading ``ipam.Prefix`` (which represents allocations, not
forwarding decisions) we introduce a dedicated model with proper schema,
list/detail views, and filter support.

Design choices:

- **CASCADE from Device.** When a Device is deleted, its routes go with it.
  Matches FortiOS semantics — routes only exist in the context of a router.
- **Interface is nullable.** Blackhole routes have no egress interface, and
  some FortiOS configurations leave the device field empty when the route
  is resolved via routing-table lookup (rare but legal).
- **destination stored as CIDR string.** Could have decomposed into
  ``network`` + ``prefix_length`` columns matching ``ipam.Prefix``, but the
  Route model never participates in containment queries — a flat CharField
  with validation is sufficient and keeps the migration straightforward.
- **(device, vdom, seq_num) is the natural key.** FortiOS uses ``seq-num``
  as the unique route identifier per device/vdom; we mirror that so the
  pull adapter has a stable identity to diff against.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from nautobot.apps.models import PrimaryModel
from nautobot.dcim.models import Device, Interface


def _validate_cidr(value: str) -> None:
    """Reject obviously-malformed CIDR input before it reaches the DB.

    Full RFC validation lives in :mod:`ipaddress`; this just ensures the
    string contains a slash and a numeric mask, so blank input or stray
    dotted-mask strings get rejected with a useful error message instead
    of a 500 on save.
    """
    if "/" not in value:
        raise ValidationError(f"{value!r} is not a CIDR — missing '/'")
    _addr, _, mask = value.partition("/")
    if not mask.isdigit():
        raise ValidationError(f"{value!r} has a non-numeric prefix length")


class FortinetStaticRoute(PrimaryModel):
    """One FortiOS ``router.static`` entry, anchored to a Nautobot Device.

    Identified by ``(device, vdom, seq_num)``. The seq_num field doubles as
    FortiOS's primary key for the route — it's how operators reference the
    route on the device CLI (``edit 1``, ``edit 2``...) and how the REST
    API addresses it (``cmdb/router/static/<seq_num>``). Mirroring it here
    means the DiffSync layer has a stable identifier for create vs. update
    decisions.
    """

    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name="fortinet_static_routes",
        help_text="The FortiGate this static route lives on.",
    )
    vdom = models.CharField(
        max_length=32,
        default="root",
        help_text="FortiOS Virtual Domain name. Routes are vdom-scoped.",
    )
    seq_num = models.PositiveIntegerField(
        help_text="FortiOS sequence number — the route's primary key on the device.",
    )
    destination = models.CharField(
        max_length=43,  # IPv6 + /128 = 39 + 1 + 3 chars; rounded to 43
        validators=[_validate_cidr],
        help_text='Destination prefix in CIDR notation (e.g. "10.20.0.0/16" or "0.0.0.0/0").',
    )
    gateway = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Next-hop IP. Null for blackhole or interface-routed entries.",
    )
    interface = models.ForeignKey(
        Interface,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fortinet_static_routes_egress",
        help_text="Egress interface on the FortiGate. Null for blackhole routes.",
    )
    distance = models.PositiveSmallIntegerField(
        default=10,
        help_text="Administrative distance. FortiOS default is 10.",
    )
    priority = models.PositiveSmallIntegerField(
        default=0,
        help_text="Route priority within the same distance. Lower = preferred.",
    )
    blackhole = models.BooleanField(
        default=False,
        help_text="If True, matching traffic is silently discarded (no egress).",
    )
    comment = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Free-form description. Synced from FortiOS comment field.",
    )

    class Meta:
        """Model metadata: ordering + composite uniqueness."""

        ordering = ["device", "vdom", "seq_num"]
        unique_together = [("device", "vdom", "seq_num")]
        verbose_name = "Fortinet Static Route"
        verbose_name_plural = "Fortinet Static Routes"

    def __str__(self) -> str:
        """Render as ``<device>:<vdom>:<seq>  <dst> via <gw>``."""
        target = "blackhole" if self.blackhole else (self.gateway or "?")
        return f"{self.device.name}:{self.vdom}:{self.seq_num}  {self.destination} via {target}"

    def get_absolute_url(self, api: bool = False) -> str:  # pragma: no cover - URL plumbing
        """Return the canonical URL for this route's detail view."""
        from django.urls import reverse

        return reverse("plugins:nautobot_ssot_fortinet:fortinetstaticroute", args=[self.pk])
