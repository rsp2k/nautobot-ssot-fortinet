"""Nautobot SSoT integration for Fortinet (FortiGate firewall + FortiAP wireless)."""

from importlib.metadata import PackageNotFoundError, version

from nautobot.apps import NautobotAppConfig

try:
    __version__ = version("nautobot-ssot-fortinet")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"


class NautobotSSoTFortinetConfig(NautobotAppConfig):
    """App configuration for the Fortinet SSoT integration."""

    name = "nautobot_ssot_fortinet"
    verbose_name = "Nautobot SSoT Fortinet"
    description = "Pull FortiGate firewall + FortiAP wireless config into Nautobot."
    version = __version__
    author = "Ryan Malloy"
    author_email = "ryan@supported.systems"
    base_url = "ssot-fortinet"
    required_settings: list[str] = []
    default_settings: dict = {}
    caching_config: dict = {}


config = NautobotSSoTFortinetConfig
