"""UI ViewSets for the Fortinet SSoT app's Django models (v3.1+).

Wires the model + form + filter + table into Nautobot's NautobotUIViewSet
machinery, which produces list/detail/create/edit/bulk views from a single
class definition. Mirrors the pattern in
``nautobot_ssot.integrations.itential.views``.

No API serializer is registered in v3.1 — the route table is read/write via
the SSoT pull Job and the Nautobot UI forms; programmatic REST access can
be added in a follow-up if operators ask for it.
"""

from __future__ import annotations

from nautobot.apps import views

from nautobot_ssot_fortinet import filters, forms, models, tables


class FortinetStaticRouteUIViewSet(views.NautobotUIViewSet):
    """Full CRUD UI for FortinetStaticRoute records."""

    bulk_update_form_class = forms.FortinetStaticRouteBulkEditForm
    filterset_class = filters.FortinetStaticRouteFilterSet
    filterset_form_class = forms.FortinetStaticRouteFilterForm
    form_class = forms.FortinetStaticRouteForm
    queryset = models.FortinetStaticRoute.objects.select_related("device", "interface").all()
    serializer_class = None  # No API serializer in v3.1 — see module docstring
    table_class = tables.FortinetStaticRouteTable
    lookup_field = "pk"
