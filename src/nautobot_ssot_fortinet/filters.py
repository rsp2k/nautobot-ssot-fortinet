"""Filter sets for the Fortinet SSoT app's Django models (v3.1+)."""

from __future__ import annotations

from nautobot.apps.filters import BaseFilterSet, SearchFilter

from nautobot_ssot_fortinet.models import FortinetStaticRoute


class FortinetStaticRouteFilterSet(BaseFilterSet):
    """Filter set for the FortinetStaticRoute list view.

    Search ``q`` matches against destination/gateway/comment using
    case-insensitive substring (icontains) — operators searching for a
    specific subnet (``10.20``) or a partial gateway (``203.0``) find
    routes without having to scroll the table.
    """

    q = SearchFilter(
        filter_predicates={
            "destination": "icontains",
            "gateway": "icontains",
            "comment": "icontains",
        }
    )

    class Meta:
        """Filterable fields exposed in the API and list view."""

        model = FortinetStaticRoute
        fields = ["device", "vdom", "seq_num", "interface", "blackhole", "distance"]
