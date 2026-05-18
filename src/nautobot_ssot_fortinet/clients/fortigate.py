"""Factory for FortiGate REST clients.

Reads connection details from a Nautobot ``ExternalIntegration`` (URL,
verify_ssl, timeout) and credentials from the linked ``SecretsGroup``.
Credentials never live in plugin config; they resolve at call time via
whatever ``Secret.provider`` is configured (environment-variable by
default in dev; HashiCorp Vault, Delinea, etc. in prod).

Two auth modes are supported, matching FortiOS REST conventions:

1. **API token** (FortiOS 5.6+, preferred) — single ``TYPE_TOKEN`` secret
2. **Username + password** (legacy fallback) — both ``TYPE_USERNAME`` and
   ``TYPE_PASSWORD`` secrets in the same group

The factory picks token mode if a TOKEN secret is present, else falls
back to user/pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured
from fortigate_api import FortiGateAPI
from nautobot.extras.choices import (
    SecretsGroupAccessTypeChoices,
    SecretsGroupSecretTypeChoices,
)

if TYPE_CHECKING:
    from nautobot.extras.models import ExternalIntegration


def build_client(external_integration: ExternalIntegration) -> FortiGateAPI:
    """Build a configured FortiGateAPI from an ExternalIntegration record.

    Args:
        external_integration: A Nautobot ``ExternalIntegration`` whose
            ``remote_url`` points at the FortiGate (e.g.
            ``https://fgt-edge1.example.com`` or ``https://fgt:8443``) and
            whose ``secrets_group`` holds either a TOKEN secret or
            USERNAME+PASSWORD secrets.

    Returns:
        A configured ``FortiGateAPI`` instance. Callers should use it as a
        context manager — ``with build_client(ext) as fgt: ...`` — so the
        session token gets revoked on exit. The library handles login on
        first use inside the context.

    Raises:
        ImproperlyConfigured: missing ``remote_url``, missing or empty
            ``secrets_group``, or neither TOKEN nor USERNAME+PASSWORD
            secrets are configured (with HTTP access type).

    """
    name = external_integration.name
    raw_url = external_integration.remote_url
    if not raw_url:
        raise ImproperlyConfigured(f"ExternalIntegration {name!r} has no remote_url set")

    group = external_integration.secrets_group
    if group is None:
        raise ImproperlyConfigured(f"ExternalIntegration {name!r} has no secrets_group set")

    host, port, scheme = _parse_remote_url(raw_url)
    verify_ssl = external_integration.verify_ssl
    timeout = external_integration.timeout or 30

    common_kwargs: dict = {
        "host": host,
        "verify": verify_ssl,
        "timeout": timeout,
        "scheme": scheme,
    }
    if port is not None:
        common_kwargs["port"] = port

    token = _safe_get_secret(group, SecretsGroupSecretTypeChoices.TYPE_TOKEN)
    if token:
        return FortiGateAPI(token=token, **common_kwargs)

    username = _safe_get_secret(group, SecretsGroupSecretTypeChoices.TYPE_USERNAME)
    password = _safe_get_secret(group, SecretsGroupSecretTypeChoices.TYPE_PASSWORD)
    if username and password:
        return FortiGateAPI(username=username, password=password, **common_kwargs)

    raise ImproperlyConfigured(
        f"SecretsGroup {group.name!r} for ExternalIntegration {name!r} has "
        f"neither a TOKEN secret nor a USERNAME+PASSWORD pair under "
        f"access_type=HTTP. If you're using the environment-variable provider, "
        f"verify the named env var is actually set inside the worker container."
    )


def _parse_remote_url(raw_url: str) -> tuple[str, int | None, str]:
    """Split a remote_url string into (host, port, scheme).

    fortigate-api wants ``host``, ``port``, and ``scheme`` as separate
    arguments — not a combined URL. We use ``urllib.parse`` to handle the
    edge cases (port, IPv6 brackets, schemeless URLs, trailing slashes).

    Returns ``port=None`` if the URL doesn't specify one (fortigate-api
    defaults to 443 for https). Scheme defaults to ``"https"`` if the URL
    is bare (just a hostname).

    >>> _parse_remote_url("https://fgt-edge1.example.com")
    ('fgt-edge1.example.com', None, 'https')
    >>> _parse_remote_url("https://fgt:8443/")
    ('fgt', 8443, 'https')
    >>> _parse_remote_url("fortigate.example.com")
    ('fortigate.example.com', None, 'https')
    >>> _parse_remote_url("http://fgt.lab:8080")
    ('fgt.lab', 8080, 'http')
    """
    raw = raw_url.strip().rstrip("/")
    # Schemeless inputs aren't parsed correctly by urlparse — it treats them
    # as a path. Prepend https:// if no scheme present.
    if "://" not in raw:
        raw = "https://" + raw

    parsed = urlparse(raw)
    if not parsed.hostname:
        raise ImproperlyConfigured(f"Could not extract hostname from URL {raw_url!r}")

    scheme = parsed.scheme or "https"
    return parsed.hostname, parsed.port, scheme


def _safe_get_secret(group, secret_type: str) -> str | None:
    """Look up a secret value, returning None when the association doesn't exist.

    ``SecretsGroup.get_secret_value`` raises
    ``SecretsGroupAssociation.DoesNotExist`` on missing keys; we want the
    absence of e.g. a TOKEN to be a clean signal to fall through to the
    username/password code path, not an exception.

    Other exceptions (env-var missing, Vault unreachable, etc.) are
    deliberately propagated — those indicate misconfiguration the operator
    must see, not a "secret not configured" case.
    """
    from nautobot.extras.models import SecretsGroupAssociation

    try:
        return group.get_secret_value(
            SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type,
        )
    except SecretsGroupAssociation.DoesNotExist:
        return None
