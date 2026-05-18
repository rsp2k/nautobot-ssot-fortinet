"""Nautobot config for the SSoT-Fortinet dev stack.

Loaded via volume mount at /opt/nautobot/nautobot_config.py inside each
Nautobot container. Imports the upstream defaults, then overrides only
what we need: PLUGINS and a couple of dev toggles.
"""

import os

# Pull in Nautobot's default settings (DB/cache/Celery wiring from env vars).
from nautobot.core.settings import *  # noqa: F401,F403
from nautobot.core.settings_funcs import is_truthy  # noqa: F401

DEBUG = is_truthy(os.environ.get("NAUTOBOT_DEBUG", "true"))

PLUGINS = [
    "nautobot_ssot",
    "nautobot_firewall_models",
    "nautobot_ssot_fortinet",
]

# Our plugin has no PLUGINS_CONFIG entries in v1 — the FortiGate client
# reads its URL and credentials from an `ExternalIntegration` chosen at
# Job-run time via an ObjectVar, not from a singleton config dict.
PLUGINS_CONFIG = {
    "nautobot_ssot": {
        "hide_example_jobs": False,
    },
}
