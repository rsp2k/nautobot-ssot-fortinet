"""URL routing for the Fortinet SSoT app (v3.1+).

The app's ``base_url`` (from ``NautobotSSoTFortinetConfig``) prefixes
everything here under ``/plugins/ssot-fortinet/``. The static-routes
viewset registers under ``/plugins/ssot-fortinet/static-routes/`` and
inherits Nautobot's standard list/detail/add/edit/delete URL shapes.
"""

from __future__ import annotations

from nautobot.apps.urls import NautobotUIViewSetRouter

from nautobot_ssot_fortinet import views

app_name = "nautobot_ssot_fortinet"

router = NautobotUIViewSetRouter()
router.register("static-routes", views.FortinetStaticRouteUIViewSet)

urlpatterns = router.urls
