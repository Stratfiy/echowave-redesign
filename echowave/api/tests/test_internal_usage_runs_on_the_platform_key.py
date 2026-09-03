"""Everything we run ourselves runs on the key staff pasted at /superadmin/provider-keys.

Three surfaces spend model tokens on Decibyl's behalf rather than a customer's:
the agent builder, knowledge-base embeddings, and the default stack a brand-new
account gets before it has chosen anything. Each resolves its key separately,
and nothing asserted that they all land on the same place — so "we have OpenAI
set in superadmin, use it for internal work" was an inference across three
modules.

The specific thing pinned here is that **one OpenAI key is enough**. If any of
these quietly needs a second vendor's key, a fresh deployment with only OpenAI
installed comes up half-working, and the half that fails is the half nobody
tests before a customer does.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from api.db.models import UserModel
from api.enums import CostComponent
from api.schemas.ai_model_configuration import (
    DecibylManagedAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration import managed_resolution
from api.services.configuration import platform_credentials as creds

TEST_SECRET = Fernet.generate_key().decode()
PLATFORM_KEY = "sk-the-only-key-installed-anywhere-0042"


@pytest.fixture(autouse=True)
def configured_secret(monkeypatch):
    monkeypatch.setattr(creds, "PLATFORM_CREDENTIAL_SECRET", TEST_SECRET)


async def _only_openai_installed(async_session):
    """The state being described: one OpenAI LLM key, nothing else.

    Nothing is monkeypatched onto ``db_client`` here. The ``db_session``
    fixture already points its ``async_session`` at this test's session, and
    ``managed_resolution`` imports that same singleton — so patching it again
    only adds a second restore of a global, whose teardown order against the
    fixture is what left a closed connection installed for every test that ran
    afterwards. Nine unrelated tests failed for it, none of them in this file.
    """
    staff = UserModel(provider_id="staff-internal-usage")
    async_session.add(staff)
    await async_session.flush()
    await creds.set_credential(
        async_session,
        actor_user_id=staff.id,
        component=CostComponent.LLM,
        provider="openai",
        api_key=PLATFORM_KEY,
        label="platform openai",
    )
    return staff


def _default_managed_config() -> object:
    """What a new account gets: managed tiers, and no MPS service key.

    The empty ``api_key`` is the point. It is the model-gateway service key,
    minted per organization against MPS, and an account provisioned while MPS
    was unreachable has none. That must not stop the stack resolving — the
    tokens are billed to us either way, and the key that authenticates to the
    vendor comes from the platform vault, not from MPS.
    """
    return compile_ai_model_configuration_v2(
        OrganizationAIModelConfigurationV2(
            version=2,
            mode="decibyl",
            decibyl=DecibylManagedAIModelConfiguration(
                api_key="",
                llm_tier="default",
                stt_tier="default",
                tts_tier="default",
            ),
        )
    )


@pytest.mark.asyncio
class TestTheManagedStack:
    async def test_the_default_llm_tier_lands_on_openai_and_our_key(
        self, db_session, async_session
    ):
        await _only_openai_installed(async_session)
        effective = _default_managed_config()

        await managed_resolution.apply(effective)

        assert effective.llm.provider == "openai"
        assert effective.llm.api_key == PLATFORM_KEY

    async def test_knowledge_base_embeddings_land_on_the_same_key(
        self, db_session, async_session
    ):
        """Embeddings have no credential slot of their own — they authenticate
        on the LLM credential, so the OpenAI key already installed serves them.
        A second slot would mean pasting the same key twice."""
        await _only_openai_installed(async_session)
        effective = _default_managed_config()

        await managed_resolution.apply(effective)

        assert effective.embeddings.provider == "openai"
        assert effective.embeddings.model == "text-embedding-3-small"
        assert effective.embeddings.api_key == PLATFORM_KEY

    async def test_a_missing_mps_service_key_does_not_stop_any_of_it(
        self, db_session, async_session
    ):
        """Stated separately because it is the failure that was actually seen:
        a signup completed while MPS was unreachable got no configuration at
        all, and every AI surface answered "requires an LLM configuration"."""
        await _only_openai_installed(async_session)
        # Built with api_key="" — see _default_managed_config.
        effective = _default_managed_config()

        await managed_resolution.apply(effective)

        assert effective.llm.api_key == PLATFORM_KEY
        assert effective.embeddings.api_key == PLATFORM_KEY


@pytest.mark.asyncio
class TestTheAgentBuilder:
    async def test_it_picks_openai_when_that_is_the_only_key_installed(
        self, db_session, async_session, monkeypatch
    ):
        """The preference order leads with Anthropic. With no Anthropic key it
        must fall through rather than refuse — otherwise the builder is dark on
        a deployment that has a perfectly good OpenAI key."""
        from api import constants
        from api.services.agent_builder import settings

        await _only_openai_installed(async_session)
        monkeypatch.setattr(constants, "AGENT_BUILDER_ENABLED", True)
        monkeypatch.setattr(settings.constants, "AGENT_BUILDER_ENABLED", True)

        model = await settings.resolve_model(async_session)

        assert model.provider == "openai"
        assert model.api_key == PLATFORM_KEY
        assert model.model  # a model name, or the turn has nothing to call
