"""Regression guard for v2.9's Job.run() instance-attr capture (added in v2.10).

Closes the test gap that allowed v2.9's ``AttributeError: 'ObjectVar' object
has no attribute 'name'`` bug to exist in v1.0–v2.8.

Context:
  - The unit tests (``tests/``) use a conftest that stubs out Nautobot and
    Django entirely; they can't exercise the real Job classes.
  - The e2e push/pull scripts (``e2e_push_*.py``) call DiffSync adapters
    directly via ``sync_from()``, bypassing the Job's ``run()`` path —
    which is exactly where v2.9's bug lived.
  - The full Job lifecycle (with Celery context, JobResult creation, etc.)
    needs Nautobot's actual web-UI/Celery dispatch path. That's covered
    end-to-end by the Playwright UI tests in the docs-screenshot session.

Scope of THIS test: the focused v2.9 contract — our ``run()`` override
captures custom form kwargs (``external_integration``, ``vdom``, etc.) as
instance attrs before forwarding to ``super().run()``. We mock the base
class's ``run()`` so we don't need Celery context, then call our override
and assert the attrs landed correctly.

If this test passes, the v2.9 bug class can't recur. If we ever refactor
a Job's form-var schema and forget to update the corresponding ``run()``
override, this test catches it before any operator does.

Run via:  make -C development e2e-jobs-lifecycle
"""

from unittest.mock import MagicMock, patch

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EXT_NAME = "fgt-dev"
VDOM = "root"


def _check_attr_capture(label, job_cls, base_run_path, expected_attrs, extra_kwargs=None):
    """Instantiate Job, call run() with mocked super().run(), assert attrs landed.

    Args:
        label: human-readable test description for the pass/fail line
        job_cls: the Job class under test
        base_run_path: import path to the base class's run() (different for
                       DataSource vs DataTarget)
        expected_attrs: dict of {attr_name: expected_value} to assert on the
                        instance after run() completes
        extra_kwargs: optional additional run() kwargs (e.g. ap_* for wireless)
    """
    from nautobot.extras.models import ExternalIntegration

    ext = ExternalIntegration.objects.get(name=EXT_NAME)

    kwargs = {
        "dryrun": True,
        "memory_profiling": False,
        "parallel_loading": False,
        "external_integration": ext,
        "vdom": VDOM,
        "delete_records_missing_from_source": False,
    }
    if extra_kwargs:
        kwargs.update(extra_kwargs)

    print(f"\n[test] {label}")

    job = job_cls()
    try:
        with patch(base_run_path) as base_run:
            job.run(**kwargs)
    except AttributeError as e:
        if "ObjectVar" in str(e) or "StringVar" in str(e) or "BooleanVar" in str(e):
            print(f"  ✗ FAIL: v2.9 REGRESSION — form var descriptor leaked: {e}")
            return False
        print(f"  ✗ FAIL: unexpected AttributeError: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ FAIL: {type(e).__name__}: {str(e)[:200]}")
        return False

    # super().run() must have been called (proves we forwarded properly)
    if not base_run.called:
        print(f"  ✗ FAIL: super().run() was never called — kwargs not forwarded")
        return False

    # Each expected attr must have landed correctly on the instance
    for attr_name, expected_value in expected_attrs.items():
        actual = getattr(job, attr_name, "<MISSING>")
        # ExternalIntegration → check .name (the field that crashed in v2.9)
        actual_repr = getattr(actual, "name", actual) if hasattr(actual, "name") else actual
        if actual_repr != expected_value:
            print(f"  ✗ FAIL: {attr_name} = {actual_repr!r} (expected {expected_value!r})")
            return False

    print(f"  ✓ PASS — all {len(expected_attrs)} instance attrs captured correctly")
    return True


def run() -> None:
    print("=" * 70)
    print("v2.9 regression guard: Job.run() instance-attr capture")
    print(f"  Tests all 4 SSoT Jobs' run() override against {EXT_NAME!r}.")
    print("  super().run() is mocked — we test the attr-capture contract only.")
    print("  Full lifecycle is verified by the Playwright UI test (session record).")
    print("=" * 70)

    from nautobot_ssot_fortinet.jobs import (
        FortiGateFirewallDataSource,
        FortiGateFirewallDataTarget,
        FortiGateWirelessDataSource,
        FortiGateWirelessDataTarget,
    )

    common_expected = {
        "external_integration": EXT_NAME,  # checks .name via the attr-name shortcut
        "vdom": VDOM,
        "delete_records_missing_from_source": False,
    }

    results = [
        _check_attr_capture(
            "FortiGateFirewallDataSource (pull) — the v2.9 reported case",
            FortiGateFirewallDataSource,
            base_run_path="nautobot_ssot.jobs.base.DataSource.run",
            expected_attrs=common_expected,
        ),
        _check_attr_capture(
            "FortiGateWirelessDataSource (pull) — has optional ap_* form vars",
            FortiGateWirelessDataSource,
            base_run_path="nautobot_ssot.jobs.base.DataSource.run",
            expected_attrs={
                **common_expected,
                "ap_device_type": None,
                "ap_role": None,
                "ap_location": None,
            },
        ),
        _check_attr_capture(
            "FortiGateFirewallDataTarget (push) — DataTarget lifecycle",
            FortiGateFirewallDataTarget,
            base_run_path="nautobot_ssot.jobs.base.DataTarget.run",
            expected_attrs=common_expected,
        ),
        _check_attr_capture(
            "FortiGateWirelessDataTarget (push) — DataTarget lifecycle",
            FortiGateWirelessDataTarget,
            base_run_path="nautobot_ssot.jobs.base.DataTarget.run",
            expected_attrs=common_expected,
        ),
    ]

    print("\n" + "=" * 70)
    passed = sum(results)
    total = len(results)
    if passed == total:
        print(f"✓ All {total} attribute-capture tests PASSED")
        print("  v2.9 regression guard in place — the AttributeError class can't recur.")
    else:
        print(f"✗ {total - passed} of {total} tests FAILED")
    print("=" * 70)
