"""Seed dev data for the SSoT-Fortinet integration.

Idempotent. Re-run is safe — uses get_or_create / update_or_create.

Creates a credential stack for ONE dev FortiGate target named ``fgt-dev``:

    Secret(s)            (provider = environment-variable, var = FGT_DEV_TOKEN
       │                     OR FGT_DEV_USERNAME + FGT_DEV_PASSWORD)
       ▼
    SecretsGroup "fgt-dev creds"
       │     - assoc (HTTP, TOKEN)    → token Secret    (if FGT_DEV_TOKEN set)
       │     - assoc (HTTP, USERNAME) → username Secret (if FGT_DEV_USERNAME set)
       │     - assoc (HTTP, PASSWORD) → password Secret (if FGT_DEV_PASSWORD set)
       ▼
    ExternalIntegration "fgt-dev"  (remote_url, verify_ssl, secrets_group FK)

Operator chooses auth mode by setting the env vars. If FGT_DEV_TOKEN is
present, the client will use token mode; otherwise it falls back to
username+password if both are present. Setting all three is fine — the
client prefers token.

Run via:  ``make -C development seed``
"""

import os

from nautobot.extras.choices import (
    SecretsGroupAccessTypeChoices,
    SecretsGroupSecretTypeChoices,
)
from nautobot.extras.models import (
    ExternalIntegration,
    Secret,
    SecretsGroup,
    SecretsGroupAssociation,
)

TARGET_NAME = "fgt-dev"
GROUP_NAME = f"{TARGET_NAME} creds"

# (secret_type, secret_display_name, env_var) for each auth mode.
SECRET_SLOTS = [
    (SecretsGroupSecretTypeChoices.TYPE_TOKEN, f"{TARGET_NAME} API token", "FGT_DEV_TOKEN"),
    (SecretsGroupSecretTypeChoices.TYPE_USERNAME, f"{TARGET_NAME} username", "FGT_DEV_USERNAME"),
    (SecretsGroupSecretTypeChoices.TYPE_PASSWORD, f"{TARGET_NAME} password", "FGT_DEV_PASSWORD"),
]


def run() -> None:
    """Entry point invoked by nautobot-server runscript."""
    print(f"=== Seeding dev FortiGate target {TARGET_NAME!r} ===")

    group, group_created = SecretsGroup.objects.get_or_create(name=GROUP_NAME)
    print(f"  SecretsGroup {GROUP_NAME!r} {'created' if group_created else 'exists'}")

    any_secret_configured = False
    for secret_type, secret_name, env_var in SECRET_SLOTS:
        env_value_present = bool(os.environ.get(env_var))

        # Always create the Secret record (so the SecretsGroup association can
        # exist even if the env var isn't set yet — operator can set the env
        # var later without re-seeding). Skip the association ONLY if the env
        # var is unset AND the association doesn't already exist.
        secret, secret_created = Secret.objects.update_or_create(
            name=secret_name,
            defaults={
                "provider": "environment-variable",
                "parameters": {"variable": env_var},
            },
        )
        status = "created" if secret_created else "updated"
        env_marker = "✓ env var set" if env_value_present else "✗ env var NOT set"
        print(f"  Secret {secret_name!r}  {status}  ({env_marker})")

        if env_value_present:
            SecretsGroupAssociation.objects.update_or_create(
                secrets_group=group,
                access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
                secret_type=secret_type,
                defaults={"secret": secret},
            )
            print(f"    → associated to SecretsGroup (HTTP, {secret_type})")
            any_secret_configured = True
        else:
            # Don't auto-remove existing associations — operator might
            # be unsetting an env var temporarily. Just warn.
            existing = SecretsGroupAssociation.objects.filter(
                secrets_group=group, secret_type=secret_type
            )
            if existing.exists():
                print("    (association exists from previous seed — leaving in place)")

    if not any_secret_configured:
        print(
            "\n  WARNING: no FGT_DEV_TOKEN / FGT_DEV_USERNAME / FGT_DEV_PASSWORD\n"
            "  env vars are set inside the container. The Job will fail with\n"
            "  ImproperlyConfigured until you set one auth mode in development/.env\n"
            "  and restart the web container."
        )

    remote_url = os.environ.get("FGT_DEV_URL", "https://fortigate.example.com")
    verify_ssl = os.environ.get("FGT_DEV_VERIFY_SSL", "true").lower() == "true"

    ext, ext_created = ExternalIntegration.objects.update_or_create(
        name=TARGET_NAME,
        defaults={
            "remote_url": remote_url,
            "verify_ssl": verify_ssl,
            "timeout": 30,
            "secrets_group": group,
        },
    )
    action = "created" if ext_created else "updated"
    print(
        f"\n  ExternalIntegration {TARGET_NAME!r}  {action}\n"
        f"    remote_url = {remote_url}\n"
        f"    verify_ssl = {verify_ssl}"
    )

    print()
    print(f"Secrets in DB: {Secret.objects.count()}")
    print(f"SecretsGroups in DB: {SecretsGroup.objects.count()}")
    print(f"ExternalIntegrations in DB: {ExternalIntegration.objects.count()}")
