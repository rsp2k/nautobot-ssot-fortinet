"""Tables for the Fortinet SSoT app's Django models (v3.1+)."""

from __future__ import annotations

import django_tables2 as tables
from nautobot.apps.tables import BaseTable, ButtonsColumn, ToggleColumn

from nautobot_ssot_fortinet.models import FortinetStaticRoute


class FortinetStaticRouteTable(BaseTable):
    """List view for FortinetStaticRoute.

    The ``destination`` column is linkified to the route's detail page —
    operators typically navigate routes by "what subnet, where does it
    go?", so destination is the natural primary identifier in the table.
    """

    pk = ToggleColumn()
    destination = tables.Column(linkify=True)
    device = tables.Column(linkify=True)
    interface = tables.Column(linkify=True)
    blackhole = tables.BooleanColumn()
    actions = ButtonsColumn(FortinetStaticRoute)

    class Meta(BaseTable.Meta):
        """Default + available columns."""

        model = FortinetStaticRoute
        fields = (
            "pk",
            "device",
            "vdom",
            "seq_num",
            "destination",
            "gateway",
            "interface",
            "distance",
            "priority",
            "blackhole",
            "comment",
            "actions",
        )
        default_columns = (
            "device",
            "vdom",
            "seq_num",
            "destination",
            "gateway",
            "interface",
            "distance",
            "blackhole",
            "actions",
        )
