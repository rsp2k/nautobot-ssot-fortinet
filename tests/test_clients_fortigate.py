"""Client factory tests — covers the URL parser + auth-mode selection logic.

The actual ``FortiGateAPI`` constructor is patched so tests don't try to
make network calls; we just assert that ``build_client()`` calls it with
the right kwargs for each (URL shape, secret config) combination.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# ---- URL parser --------------------------------------------------------


class TestParseRemoteUrl:
    """``_parse_remote_url`` handles the URL-shape edge cases."""

    def _parser(self):
        from nautobot_ssot_fortinet.clients.fortigate import _parse_remote_url

        return _parse_remote_url

    def test_full_https_no_port(self):
        assert self._parser()("https://fgt.example.com") == ("fgt.example.com", None, "https")

    def test_https_with_port(self):
        assert self._parser()("https://fgt:8443/") == ("fgt", 8443, "https")

    def test_schemeless_defaults_to_https(self):
        assert self._parser()("fortigate.example.com") == ("fortigate.example.com", None, "https")

    def test_http_scheme_preserved(self):
        # Edge case: lab FortiGate over plain HTTP. We respect the scheme.
        assert self._parser()("http://fgt.lab:8080") == ("fgt.lab", 8080, "http")

    def test_trailing_slash_stripped(self):
        assert self._parser()("https://fgt.example.com/") == ("fgt.example.com", None, "https")

    def test_ip_address_host(self):
        assert self._parser()("https://10.99.0.1") == ("10.99.0.1", None, "https")

    def test_invalid_url_raises(self):
        from django.core.exceptions import ImproperlyConfigured

        with pytest.raises(ImproperlyConfigured, match="Could not extract hostname"):
            self._parser()("://")


# ---- build_client ------------------------------------------------------


def _ext(
    *,
    name="fgt-test",
    url="https://fgt.example.com",
    secrets_group=None,
    verify_ssl=True,
    timeout=30,
):
    """Build a fake ExternalIntegration record."""
    ei = MagicMock()
    ei.name = name
    ei.remote_url = url
    ei.secrets_group = secrets_group
    ei.verify_ssl = verify_ssl
    ei.timeout = timeout
    return ei


def _group_with_token(token_value="ABCD1234"):
    """Build a fake SecretsGroup whose get_secret_value returns a token."""

    def side_effect(access, secret_type):
        # Token type → return value; other types → raise DoesNotExist
        type_module = sys.modules["nautobot.extras.choices"]
        if secret_type == type_module.SecretsGroupSecretTypeChoices.TYPE_TOKEN:
            return token_value
        raise sys.modules["nautobot.extras.models"].SecretsGroupAssociation.DoesNotExist()

    g = MagicMock()
    g.name = "test-creds"
    g.get_secret_value.side_effect = side_effect
    return g


def _group_with_userpass(username="admin", password="secret"):
    def side_effect(access, secret_type):
        type_module = sys.modules["nautobot.extras.choices"]
        if secret_type == type_module.SecretsGroupSecretTypeChoices.TYPE_USERNAME:
            return username
        if secret_type == type_module.SecretsGroupSecretTypeChoices.TYPE_PASSWORD:
            return password
        raise sys.modules["nautobot.extras.models"].SecretsGroupAssociation.DoesNotExist()

    g = MagicMock()
    g.name = "test-creds"
    g.get_secret_value.side_effect = side_effect
    return g


def _empty_group():
    def side_effect(access, secret_type):
        raise sys.modules["nautobot.extras.models"].SecretsGroupAssociation.DoesNotExist()

    g = MagicMock()
    g.name = "empty-creds"
    g.get_secret_value.side_effect = side_effect
    return g


class TestBuildClientValidation:
    """Error paths that catch operator misconfiguration."""

    def test_missing_remote_url_raises(self):
        from django.core.exceptions import ImproperlyConfigured

        from nautobot_ssot_fortinet.clients.fortigate import build_client

        with pytest.raises(ImproperlyConfigured, match="no remote_url"):
            build_client(_ext(url=""))

    def test_none_remote_url_raises(self):
        from django.core.exceptions import ImproperlyConfigured

        from nautobot_ssot_fortinet.clients.fortigate import build_client

        with pytest.raises(ImproperlyConfigured, match="no remote_url"):
            build_client(_ext(url=None))

    def test_missing_secrets_group_raises(self):
        from django.core.exceptions import ImproperlyConfigured

        from nautobot_ssot_fortinet.clients.fortigate import build_client

        with pytest.raises(ImproperlyConfigured, match="no secrets_group"):
            build_client(_ext(secrets_group=None))

    def test_empty_secrets_group_raises_with_helpful_message(self):
        from django.core.exceptions import ImproperlyConfigured

        from nautobot_ssot_fortinet.clients.fortigate import build_client

        with pytest.raises(ImproperlyConfigured, match="USERNAME\\+PASSWORD"):
            build_client(_ext(secrets_group=_empty_group()))


class TestBuildClientTokenMode:
    """The preferred auth path — single API token."""

    @patch("nautobot_ssot_fortinet.clients.fortigate.FortiGateAPI")
    def test_token_passed_to_fortigate_api(self, mock_cls):
        from nautobot_ssot_fortinet.clients.fortigate import build_client

        build_client(_ext(secrets_group=_group_with_token("MYTOKEN")))

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["token"] == "MYTOKEN"
        assert kwargs["host"] == "fgt.example.com"
        assert kwargs["scheme"] == "https"
        assert kwargs["verify"] is True
        assert kwargs["timeout"] == 30
        assert "port" not in kwargs  # default port — don't pass explicitly
        # Should NOT have user/pass when token mode wins
        assert "username" not in kwargs
        assert "password" not in kwargs

    @patch("nautobot_ssot_fortinet.clients.fortigate.FortiGateAPI")
    def test_token_mode_wins_over_userpass_when_both_present(self, mock_cls):
        from nautobot_ssot_fortinet.clients.fortigate import build_client

        # Group with TOKEN, USERNAME, AND PASSWORD all populated
        def side_effect(access, secret_type):
            tm = sys.modules["nautobot.extras.choices"]
            t = tm.SecretsGroupSecretTypeChoices
            mapping = {
                t.TYPE_TOKEN: "TOKEN_VAL",
                t.TYPE_USERNAME: "admin",
                t.TYPE_PASSWORD: "secret",
            }
            return mapping[secret_type]

        g = MagicMock()
        g.name = "rich-creds"
        g.get_secret_value.side_effect = side_effect

        build_client(_ext(secrets_group=g))
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["token"] == "TOKEN_VAL"
        assert "username" not in kwargs

    @patch("nautobot_ssot_fortinet.clients.fortigate.FortiGateAPI")
    def test_https_with_port(self, mock_cls):
        from nautobot_ssot_fortinet.clients.fortigate import build_client

        build_client(_ext(url="https://fgt:8443", secrets_group=_group_with_token()))
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["host"] == "fgt"
        assert kwargs["port"] == 8443

    @patch("nautobot_ssot_fortinet.clients.fortigate.FortiGateAPI")
    def test_verify_ssl_false_propagates(self, mock_cls):
        from nautobot_ssot_fortinet.clients.fortigate import build_client

        build_client(_ext(secrets_group=_group_with_token(), verify_ssl=False))
        assert mock_cls.call_args.kwargs["verify"] is False

    @patch("nautobot_ssot_fortinet.clients.fortigate.FortiGateAPI")
    def test_zero_timeout_falls_back_to_30(self, mock_cls):
        from nautobot_ssot_fortinet.clients.fortigate import build_client

        build_client(_ext(secrets_group=_group_with_token(), timeout=0))
        assert mock_cls.call_args.kwargs["timeout"] == 30


class TestBuildClientUserPassMode:
    """Fallback auth path — username + password (FortiOS pre-5.6)."""

    @patch("nautobot_ssot_fortinet.clients.fortigate.FortiGateAPI")
    def test_userpass_used_when_no_token(self, mock_cls):
        from nautobot_ssot_fortinet.clients.fortigate import build_client

        build_client(_ext(secrets_group=_group_with_userpass("admin", "p@ss")))
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["username"] == "admin"
        assert kwargs["password"] == "p@ss"
        assert "token" not in kwargs

    @patch("nautobot_ssot_fortinet.clients.fortigate.FortiGateAPI")
    def test_partial_userpass_falls_through_to_error(self, mock_cls):
        """User-only (no password) should NOT silently succeed."""
        from django.core.exceptions import ImproperlyConfigured

        from nautobot_ssot_fortinet.clients.fortigate import build_client

        def side_effect(access, secret_type):
            tm = sys.modules["nautobot.extras.choices"]
            t = tm.SecretsGroupSecretTypeChoices
            if secret_type == t.TYPE_USERNAME:
                return "admin"
            # No TOKEN, no PASSWORD
            raise sys.modules["nautobot.extras.models"].SecretsGroupAssociation.DoesNotExist()

        g = MagicMock()
        g.name = "partial-creds"
        g.get_secret_value.side_effect = side_effect

        with pytest.raises(ImproperlyConfigured, match="USERNAME\\+PASSWORD"):
            build_client(_ext(secrets_group=g))
        mock_cls.assert_not_called()
