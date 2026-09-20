"""The shipped JWT secret must not be able to sign a real deployment's tokens.

``OSS_JWT_SECRET`` defaulted to the literal ``change-me-in-production``. That
default is in the repository, in the deploy templates and in every fork, so on
any deployment that did not set the variable the signing key was public
knowledge: anyone could mint a token for any ``user_id`` and be that user.

The failure mode is what makes it worth a guard rather than a line in a runbook.
Nothing is broken by leaving the default in place — signup works, login works,
sessions persist — so there is no symptom to notice and no point at which
somebody is prompted to look. It is exactly the shape ``AGENTS.md`` calls silent
absence, except that what goes missing is the authentication.

So the process refuses to start. A deployment that cannot sign tokens safely
should not serve requests while pretending it can, and the loud failure happens
at boot, in front of whoever is doing the deploy, rather than months later in
front of whoever found the key.

The check is scoped to the deployments where the secret is actually used to
authenticate people (``AUTH_PROVIDER == "local"``) and skipped in dev and test
environments, where a fixed key is how the suite runs at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.constants import PLACEHOLDER_JWT_SECRET, verify_jwt_secret_is_safe


def test_the_shipped_placeholder_is_still_the_string_we_guard_against():
    """If the default is renamed, this guard must be renamed with it."""
    assert PLACEHOLDER_JWT_SECRET == "change-me-in-production"


def test_a_production_deployment_refuses_to_start_on_the_placeholder():
    with pytest.raises(RuntimeError) as exc:
        verify_jwt_secret_is_safe(
            secret=PLACEHOLDER_JWT_SECRET,
            environment="production",
            auth_provider="local",
        )
    # The message has to say which variable to set; a boot failure that does
    # not name its own fix is a boot failure somebody works around.
    assert "OSS_JWT_SECRET" in str(exc.value)


def test_an_empty_secret_is_refused_too():
    """Unset is not safer than the placeholder — it signs with nothing."""
    for missing in ("", "   ", None):
        with pytest.raises(RuntimeError):
            verify_jwt_secret_is_safe(
                secret=missing,
                environment="production",
                auth_provider="local",
            )


def test_a_real_secret_is_accepted():
    verify_jwt_secret_is_safe(
        secret="a-real-secret-set-by-the-operator",
        environment="production",
        auth_provider="local",
    )


def test_dev_and_test_environments_keep_the_placeholder():
    """The suite and a laptop run on the default; only real deployments refuse."""
    for environment in ("local", "test", "development", "dev"):
        verify_jwt_secret_is_safe(
            secret=PLACEHOLDER_JWT_SECRET,
            environment=environment,
            auth_provider="local",
        )


def test_a_deployment_that_does_not_sign_its_own_tokens_is_not_blocked():
    """Stack Auth deployments never call ``create_jwt_token``, so the
    placeholder cannot authenticate anyone there. Refusing to boot over an
    unused variable would be a false alarm that teaches people to set it to
    anything at all."""
    verify_jwt_secret_is_safe(
        secret=PLACEHOLDER_JWT_SECRET,
        environment="production",
        auth_provider="stackauth",
    )


def test_importing_the_app_on_a_production_placeholder_actually_dies():
    """The rule above is only worth having if it runs at boot.

    A subprocess rather than a reimport: ``api.constants`` is already in
    ``sys.modules`` for this process, and a guard that is only ever called by
    its own unit test is the guard the connector routes had.
    """
    import os
    import subprocess
    import sys

    environment = dict(os.environ)
    environment["ENVIRONMENT"] = "production"
    environment["AUTH_PROVIDER"] = "local"
    environment["OSS_JWT_SECRET"] = PLACEHOLDER_JWT_SECRET

    result = subprocess.run(
        [sys.executable, "-c", "import api.constants"],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert result.returncode != 0, "the placeholder booted a production process"
    assert "OSS_JWT_SECRET" in result.stderr
