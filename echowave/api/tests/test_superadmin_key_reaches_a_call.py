"""The managed tier, end to end: staff store a key, a customer's call uses it.

Two halves of this already had tests — ``test_platform_credentials`` proves a
key can be stored and never read back, ``test_managed_resolution`` proves a
managed section resolves to a vendor and a key. Nothing joined them, so
"superadmin adds a key on /superadmin/provider-keys and a customer's agent
picks it up" was an inference across two test files rather than something
asserted.

It is the inference the whole managed tier rests on: a customer with no key of
their own runs on ours and is billed at our rate. If it breaks, every managed
account stops working at once, and the previous failure mode was that the call
connected and the caller heard nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from api.db.models import UserModel
from api.enums import CostComponent
from api.services.configuration import managed_resolution
from api.services.configuration import platform_credentials as creds
from api.services.configuration.registry import OpenAILLMService

TEST_SECRET = Fernet.generate_key().decode()
PLATFORM_KEY = "sk-platform-key-the-staff-pasted-0042"


@pytest.fixture(autouse=True)
def configured_secret(monkeypatch):
    monkeypatch.setattr(creds, "PLATFORM_CREDENTIAL_SECRET", TEST_SECRET)


async def _staff(session) -> UserModel:
    user = UserModel(provider_id="staff-e2e-managed")
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
class TestAStaffKeyReachesACustomersCall:
    async def test_a_managed_section_resolves_to_the_stored_platform_key(
        self, db_session, async_session, monkeypatch
    ):
        """What `/superadmin/provider-keys` is for.

        Staff paste a key; a customer whose slot is set to Decibyl's models --
        holding no key of their own -- has it substituted at dial time.
        """
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai",
            api_key=PLATFORM_KEY,
            label="platform openai",
        )

        # `managed_resolution` opens its own session; point it at the test one
        # so it reads the row just written rather than a different database.
        monkeypatch.setattr(
            "api.services.configuration.managed_resolution.db_client.async_session",
            lambda: _SessionHandle(async_session),
        )

        # The direct managed path: the customer picked a real vendor and model
        # and asked to run it on our key.
        effective = SimpleNamespace(
            llm=OpenAILLMService(api_key="", model="gpt-4o", use_platform_key=True),
            stt=None,
            tts=None,
            realtime=None,
            embeddings=None,
        )

        await managed_resolution.apply(effective)

        assert effective.llm.api_key == PLATFORM_KEY

    async def test_a_deactivated_platform_key_is_not_handed_out(
        self, db_session, async_session, monkeypatch
    ):
        """Taking a key out of service has to actually take it out of service,
        or rotating at the vendor hands every managed account a dead key."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai",
            api_key=PLATFORM_KEY,
        )
        await creds.set_active(
            async_session,
            component=CostComponent.LLM,
            provider="openai",
            is_active=False,
        )

        monkeypatch.setattr(
            "api.services.configuration.managed_resolution.db_client.async_session",
            lambda: _SessionHandle(async_session),
        )

        effective = SimpleNamespace(
            llm=OpenAILLMService(api_key="", model="gpt-4o", use_platform_key=True),
            stt=None,
            tts=None,
            realtime=None,
            embeddings=None,
        )

        await managed_resolution.apply(effective)

        assert effective.llm.api_key != PLATFORM_KEY

    async def test_the_customer_is_billed_for_it(
        self, db_session, async_session, monkeypatch
    ):
        """The stamp that decides who pays.

        A section running on our key must read "managed" by the time billing
        looks at it, or the inference we paid for is charged to nobody.
        """
        from api.services.configuration import byok_resolution

        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai",
            api_key=PLATFORM_KEY,
        )
        monkeypatch.setattr(
            "api.services.configuration.managed_resolution.db_client.async_session",
            lambda: _SessionHandle(async_session),
        )

        effective = SimpleNamespace(
            llm=OpenAILLMService(api_key="", model="gpt-4o", use_platform_key=True),
            stt=None,
            tts=None,
            realtime=None,
            embeddings=None,
        )

        # The real order at dial time.
        await byok_resolution.apply(effective, organization_id=None)
        await managed_resolution.apply(effective)

        assert effective.llm.key_source == "managed"
        assert effective.llm.api_key == PLATFORM_KEY


class _SessionHandle:
    """Lend the test's session to code that opens its own.

    ``async with db_client.async_session()`` would otherwise start a second
    session on a second engine, which cannot see rows the test has only
    flushed. Exiting must not close the borrowed session -- the test still
    needs it.
    """

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_exc):
        return False
