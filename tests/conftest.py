"""Pytest bootstrap — stub out heavy framework imports for unit tests.

The package's ``__init__.py`` imports ``nautobot.apps.NautobotAppConfig``
at top level, which requires the entire Django + Nautobot install. For
schema-only unit tests against the DiffSync models and the FortiGate
adapter (which takes a pre-built client as a constructor arg, so it never
needs Django or a real FortiGate connection), we install lightweight
``MagicMock`` placeholders for every external module our package imports
*before* pytest collects any test.

**Exception classes** referenced in ``except`` clauses (e.g.
``SecretsGroupAssociation.DoesNotExist``) cannot be MagicMocks — Python
will raise ``TypeError: catching classes that do not inherit from
BaseException``. For those, we install real ``Exception`` subclasses on
the mock modules so the ``except`` machinery works during unit tests.

Tests that need real Nautobot/Django machinery should live in a separate
``tests/integration/`` tree and run inside the dev container.
"""

import sys
from unittest.mock import MagicMock

_FAKE_MODULES = [
    "nautobot",
    "nautobot.apps",
    "nautobot.apps.jobs",
    "nautobot.apps.models",
    "nautobot.dcim",
    "nautobot.dcim.models",
    "nautobot.extras",
    "nautobot.extras.choices",
    "nautobot.extras.models",
    "nautobot.ipam",
    "nautobot.ipam.models",
    "nautobot.wireless",
    "nautobot.wireless.models",
    "nautobot_firewall_models",
    "nautobot_firewall_models.models",
    "nautobot_ssot",
    "nautobot_ssot.jobs",
    "nautobot_ssot.jobs.base",
    "django",
    "django.conf",
    "django.core",
    "django.core.exceptions",
    "django.core.validators",
    "django.db",
    "django.db.models",
]

for name in _FAKE_MODULES:
    sys.modules.setdefault(name, MagicMock())


# ---- Real exception classes -----------------------------------------------
#
# Production code does e.g.:
#     except SecretsGroupAssociation.DoesNotExist:
#         ...
# Python validates that the caught type is a BaseException subclass at the
# ``except`` line itself, BEFORE any exception is raised. With MagicMock as
# the module, ``DoesNotExist`` is also a MagicMock — not a class — so the
# except clause raises ``TypeError: catching classes that do not inherit
# from BaseException``. Override the relevant attributes with actual
# Exception subclasses for the bits of code under test.


class _StubDoesNotExist(Exception):
    """Stand-in for Django Model.DoesNotExist exceptions in unit tests."""


class _StubImproperlyConfigured(Exception):
    """Stand-in for django.core.exceptions.ImproperlyConfigured."""


sys.modules["django.core.exceptions"].ImproperlyConfigured = _StubImproperlyConfigured

# All the Model.DoesNotExist attributes the production code catches.
sys.modules["nautobot.extras.models"].SecretsGroupAssociation = MagicMock()
sys.modules["nautobot.extras.models"].SecretsGroupAssociation.DoesNotExist = _StubDoesNotExist
sys.modules["nautobot.extras.models"].Secret = MagicMock()
sys.modules["nautobot.extras.models"].SecretsGroup = MagicMock()
sys.modules["nautobot.extras.models"].ExternalIntegration = MagicMock()
sys.modules["nautobot.extras.models"].Status = MagicMock()
sys.modules["nautobot.extras.models"].Role = MagicMock()


# ---- Stable enum values for SecretsGroupSecretTypeChoices -----------------
#
# The production code compares ``secret_type == TYPE_TOKEN`` etc. With
# MagicMock these would all be distinct (and unequal) mock objects, which
# defeats the comparison. Give them stable string values so test assertions
# can reason about which branch ran.

_choices = sys.modules["nautobot.extras.choices"]
_choices.SecretsGroupAccessTypeChoices = MagicMock()
_choices.SecretsGroupAccessTypeChoices.TYPE_HTTP = "Generic"
_choices.SecretsGroupSecretTypeChoices = MagicMock()
_choices.SecretsGroupSecretTypeChoices.TYPE_TOKEN = "token"
_choices.SecretsGroupSecretTypeChoices.TYPE_USERNAME = "username"
_choices.SecretsGroupSecretTypeChoices.TYPE_PASSWORD = "password"
