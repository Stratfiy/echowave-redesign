"""A new account is usable before its owner has chosen anything.

Provisioning asks the model gateway (MPS) for a service key and builds the
default stack from the answer. When that call fails — MPS down, no secret
configured, an OSS deployment that never had one — the whole block was
swallowed by one ``except``, and the account was left with no model
configuration at all.

That is not a degraded account, it is a dead one: every AI surface reads the
same missing configuration and refuses. It is also silent, and the only fix is
for the owner to find the model screen themselves, which is the opposite of the
ten-minute setup the product is sold on.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.auth import provisioning


@pytest.fixture
def account(monkeypatch):
    """A provisioning run with everything but the model configuration stubbed."""
    organization = SimpleNamespace(id=77)
    db = SimpleNamespace(
        get_or_create_organization_by_provider_id=AsyncMock(
            return_value=(organization, True)
        ),
        add_user_to_organization=AsyncMock(),
        update_user_selected_organization=AsyncMock(),
        update_user_configuration=AsyncMock(),
        upsert_configuration=AsyncMock(),
    )
    monkeypatch.setattr(provisioning, "db_client", db)
    monkeypatch.setattr(provisioning.announce, "announce", AsyncMock())

    saved = []
    monkeypatch.setattr(
        provisioning,
        "upsert_organization_ai_model_configuration_v2",
        AsyncMock(side_effect=lambda org_id, config: saved.append((org_id, config))),
    )
    return SimpleNamespace(db=db, saved=saved)


user = SimpleNamespace(id=5, provider_id="new-user", email="")


@pytest.mark.asyncio
class TestWhenTheModelGatewayIsUnreachable:
    async def test_the_account_still_gets_a_model_configuration(
        self, account, monkeypatch
    ):
        monkeypatch.setattr(
            provisioning,
            "create_user_configuration_with_mps_key",
            AsyncMock(side_effect=RuntimeError("MPS unreachable")),
        )

        await provisioning.provision_new_account(user)

        assert account.saved, "a new account was left with no model configuration"
        org_id, config = account.saved[0]
        assert org_id == 77
        assert config.mode == "decibyl"

    async def test_a_gateway_that_answers_with_nothing_counts_as_a_failure(
        self, account, monkeypatch
    ):
        """MPS returns None rather than raising on several of its own error
        paths. Treating that as success is how the original bug got through."""
        monkeypatch.setattr(
            provisioning,
            "create_user_configuration_with_mps_key",
            AsyncMock(return_value=None),
        )

        await provisioning.provision_new_account(user)

        assert account.saved

    async def test_the_default_runs_on_platform_keys_not_a_gateway_key(
        self, account, monkeypatch
    ):
        """The fallback exists precisely because there is no service key. If it
        wrote one it would be inventing a credential that authenticates to
        nothing."""
        monkeypatch.setattr(
            provisioning,
            "create_user_configuration_with_mps_key",
            AsyncMock(return_value=None),
        )

        await provisioning.provision_new_account(user)

        _, config = account.saved[0]
        assert config.decibyl.api_key == ""

    async def test_a_failed_default_still_leaves_the_account_created(
        self, account, monkeypatch
    ):
        """Somebody's account is worth more than a default stack."""
        monkeypatch.setattr(
            provisioning,
            "create_user_configuration_with_mps_key",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            provisioning,
            "upsert_organization_ai_model_configuration_v2",
            AsyncMock(side_effect=RuntimeError("database is on fire")),
        )

        organization = await provisioning.provision_new_account(user)

        assert organization.id == 77


@pytest.mark.asyncio
class TestWhenTheModelGatewayAnswers:
    async def test_the_gateway_configuration_is_not_overwritten(
        self, account, monkeypatch
    ):
        """The MPS stack carries the account's service key, which is minted
        once and has no second copy. Writing the keyless default over it would
        destroy the credential rather than clear a field."""
        monkeypatch.setattr(
            provisioning,
            "create_user_configuration_with_mps_key",
            AsyncMock(return_value=SimpleNamespace()),
        )
        monkeypatch.setattr(
            provisioning,
            "convert_legacy_ai_model_configuration_to_v2",
            lambda config: SimpleNamespace(model_dump=lambda **_: {"version": 2}),
        )

        await provisioning.provision_new_account(user)

        assert not account.saved
        account.db.upsert_configuration.assert_awaited()
