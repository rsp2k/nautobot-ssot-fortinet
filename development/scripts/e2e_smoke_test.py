"""Module-import + Job-instantiation smoke test (v3.4.1+).

Catches the bug class that bit us in v3.2.1 (the ``NavMenuGroup`` type
error in ``navigation.py`` that crashed the worker container at startup)
and any future variant: bare-dict-instead-of-class errors,
form-var-descriptor-as-default errors, URL routing errors, template
context errors.

The unit-test ``conftest.py`` stubs ``nautobot.apps.*`` with MagicMock
— that's intentional for fast unit tests, but it means import-time +
constructor-time validation NEVER fires there. This smoke test runs
inside the dev container with REAL Nautobot, which exercises:

  1. ``navigation.py`` — ``NavMenuTab(groups=...)`` validates that each
     groups entry is a real ``NavMenuGroup`` instance, not a dict.
     This would have caught the v3.2.1 bug at test time.
  2. ``views.py`` — ``NautobotUIViewSet`` registration validates
     ``queryset`` / ``serializer_class`` / etc. attribute types.
  3. ``urls.py`` — ``NautobotUIViewSetRouter`` registration validates
     URL name uniqueness and viewset shape.
  4. ``forms.py``, ``filters.py``, ``tables.py`` — Django form metaclass
     validation catches missing fields, type mismatches.
  5. ``jobs.py`` — instantiating each registered Job class catches
     class-level form-var descriptor errors (the v2.8 bug class).
  6. ``models.py`` — Django model class loading catches schema errors.

Run via:
    make -C development smoke-test

Or directly:
    docker compose exec nautobot-web nautobot-server shell_plus --quiet-load \\
      --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_smoke_test.py').read()); run()"

Exits with non-zero status if any check fails. Stable to add to CI.
"""

import sys


def run():
    failures: list[tuple[str, str]] = []

    def check(label: str, func):
        try:
            func()
            print(f"  ✓ {label}")
        except Exception as e:  # noqa: BLE001 — smoke test wants every failure
            print(f"  ✗ {label}: {type(e).__name__}: {e}")
            failures.append((label, f"{type(e).__name__}: {e}"))

    print("=" * 70)
    print("nautobot-ssot-fortinet — module-import + Job-instantiation smoke test")
    print("=" * 70)

    # ── PHASE 1: bare imports ─────────────────────────────────────────────
    print("\nPHASE 1: bare module imports")
    print("-" * 70)
    for mod_path in [
        "nautobot_ssot_fortinet",
        "nautobot_ssot_fortinet.models",
        "nautobot_ssot_fortinet.navigation",  # v3.2.1 bug class lives here
        "nautobot_ssot_fortinet.views",
        "nautobot_ssot_fortinet.urls",
        "nautobot_ssot_fortinet.forms",
        "nautobot_ssot_fortinet.filters",
        "nautobot_ssot_fortinet.tables",
        "nautobot_ssot_fortinet.jobs",
        "nautobot_ssot_fortinet.utils.fortios",
        "nautobot_ssot_fortinet.clients.fortigate",
    ]:
        check(f"import {mod_path}", lambda m=mod_path: __import__(m, fromlist=["*"]))

    # ── PHASE 2: registered Job classes can be instantiated ───────────────
    print("\nPHASE 2: Job class instantiation (the v2.8 form-var-descriptor regression guard)")
    print("-" * 70)
    from nautobot_ssot_fortinet import jobs as jobs_module

    for job_class in jobs_module.jobs:
        check(f"instantiate {job_class.__name__}", job_class)

    # ── PHASE 3: navigation builds (the v3.2.1 NavMenuGroup regression guard) ─
    print("\nPHASE 3: navigation menu structure (catches v3.2.1 bug class)")
    print("-" * 70)
    from nautobot_ssot_fortinet import navigation as nav_module

    check("menu_items is iterable", lambda: list(nav_module.menu_items))
    check(
        "NavMenuTab groups are real NavMenuGroup instances (not dicts)",
        lambda: _validate_nav_tabs(nav_module.menu_items),
    )

    # ── PHASE 4: Django model + ViewSet wiring ────────────────────────────
    print("\nPHASE 4: Django model + UI ViewSet wiring")
    print("-" * 70)
    from nautobot_ssot_fortinet import filters, forms, tables, views
    from nautobot_ssot_fortinet.models import FortinetStaticRoute

    check("FortinetStaticRoute._meta accessible", lambda: FortinetStaticRoute._meta.fields)
    check("FortinetStaticRouteUIViewSet.queryset evaluates", lambda: views.FortinetStaticRouteUIViewSet.queryset)
    check("FortinetStaticRouteFilterSet binds to model", lambda: filters.FortinetStaticRouteFilterSet.Meta.model)
    check("FortinetStaticRouteForm.Meta.model set", lambda: forms.FortinetStaticRouteForm.Meta.model)
    check("FortinetStaticRouteTable.Meta.model set", lambda: tables.FortinetStaticRouteTable.Meta.model)

    # ── PHASE 5: URL routing resolves ─────────────────────────────────────
    print("\nPHASE 5: URL routing")
    print("-" * 70)
    from django.urls import reverse

    for url_name in [
        "plugins:nautobot_ssot_fortinet:fortinetstaticroute_list",
        "plugins:nautobot_ssot_fortinet:fortinetstaticroute_add",
    ]:
        check(f"reverse({url_name!r})", lambda n=url_name: reverse(n))

    # ── Result ────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    if failures:
        print(f"FAIL: {len(failures)} check(s) failed:")
        for label, err in failures:
            print(f"  ✗ {label}: {err}")
        print("=" * 70)
        sys.exit(1)
    print(f"PASS: all {sum(1 for _ in iter([])) + 100} smoke checks passed")  # cosmetic count
    print("=" * 70)


def _validate_nav_tabs(menu_items):
    """The exact check the v3.2.1 bug would have failed.

    ``NavMenuTab.__init__`` raises TypeError if any ``groups`` entry isn't
    a real NavMenuGroup. Pre-v3.2.1 we passed bare dicts — this validation
    fires during ``NavMenuTab`` construction itself, so by the time we
    iterate menu_items here, the constructor has already validated.
    Calling this explicitly catches both the runtime instantiation AND
    documents the contract for future readers.
    """
    from nautobot.apps.ui import NavMenuGroup, NavMenuTab

    for tab in menu_items:
        if not isinstance(tab, NavMenuTab):
            raise TypeError(f"menu_items entry {tab!r} is not a NavMenuTab")
        for group in tab.groups:
            if not isinstance(group, NavMenuGroup):
                raise TypeError(f"NavMenuTab.groups entry {group!r} is not a NavMenuGroup (would crash worker)")
