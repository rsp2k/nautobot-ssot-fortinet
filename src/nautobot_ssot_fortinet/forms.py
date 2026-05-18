"""Forms for the Fortinet SSoT app's Django models (v3.1+).

Three forms per model is the Nautobot convention:
    1. ``NautobotModelForm`` — the add/edit form
    2. ``BulkEditForm`` — bulk-update from the list view
    3. ``FilterForm`` — the filter widget rendered above the list view

For ``FortinetStaticRoute``, manual edits via these forms are uncommon
(SSoT sync is the primary writer) but they're useful for one-off operator
tweaks: tagging a route, fixing a typo'd comment, or marking a route as
blackhole pending firewall review.
"""

from __future__ import annotations

from django import forms
from nautobot.apps.forms import BootstrapMixin, BulkEditForm, NautobotModelForm

from nautobot_ssot_fortinet.models import FortinetStaticRoute


class FortinetStaticRouteForm(NautobotModelForm):
    """Create / edit form for a single FortinetStaticRoute."""

    class Meta:
        """All editable fields exposed."""

        model = FortinetStaticRoute
        fields = "__all__"


class FortinetStaticRouteBulkEditForm(BootstrapMixin, BulkEditForm):
    """Bulk-edit form — distance + comment + blackhole are the safe targets."""

    pk = forms.ModelMultipleChoiceField(
        queryset=FortinetStaticRoute.objects.all(),
        widget=forms.MultipleHiddenInput,
    )
    distance = forms.IntegerField(required=False, min_value=0, max_value=255)
    priority = forms.IntegerField(required=False, min_value=0, max_value=65535)
    blackhole = forms.NullBooleanField(required=False)
    comment = forms.CharField(required=False, max_length=255)

    class Meta:
        """No nullable fields — leaving blank means 'no change'."""

        nullable_fields = ["comment"]


class FortinetStaticRouteFilterForm(BootstrapMixin, forms.Form):
    """Filter sidebar inputs for the list view."""

    q = forms.CharField(required=False, label="Search")
    vdom = forms.CharField(required=False)
    blackhole = forms.NullBooleanField(required=False)
