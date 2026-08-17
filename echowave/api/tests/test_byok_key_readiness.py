"""What happens when a slot is on the account's own key and there is no key.

Before this, nothing happened: the section kept its vendor and an empty string,
the pipeline built a service that could not authenticate, and the caller heard
silence for the length of the call while we paid the carrier for the minutes.
Neither the caller nor the operator was told anything.

There are exactly two defensible answers, and the account picks between them
with ``OrganizationPreferences.byok_fallback_to_managed``. The tests here are
about that choice being honoured in both directions, and about the default
being the one that cannot surprise anyone with a bill.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.services.configuration import byok_resolution, key_readiness
from api.services.configuration.registry import OpenAILLMService


def _byok_llm_with_no_inline_key():
    """A slot naming a vendor with an empty key -- "the key is in the vault"."""
    return SimpleNamespace(
        llm=OpenAILLMService(api_key="", model="gpt-4o"),
        stt=None,
        tts=None,
        realtime=None,
        embeddings=None,
        is_realtime=False,
    )


@pytest.fixture
def empty_vault(monkeypatch):
    """The account holds no usable key -- never added, or paused mid-rotation."""

    async def _none(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "api.services.configuration.organization_credentials.resolve_api_key",
        _none,
    )


@pytest.mark.asyncio
class TestTheDefaultIsToRefuse:
    async def test_an_unfillable_slot_is_left_unresolved(self, empty_vault):
        """Not switched to our key behind the customer's back.

        Running it on ours would work, and would bill them at the published
        rate for a choice they never made. The section is left alone and
        reported, so the gate can refuse the call.
        """
        effective = _byok_llm_with_no_inline_key()

        unresolved = await byok_resolution.apply(effective, organization_id=7)

        assert unresolved == ["llm"]
        assert effective.llm.use_platform_key is False
        assert effective.llm.key_source == "byok"

    async def test_the_gate_refuses_and_names_the_slot(self, empty_vault):
        """A refusal naming a field nobody can find is a refusal nobody can
        act on, so it uses the word the screen uses."""
        effective = _byok_llm_with_no_inline_key()

        with pytest.raises(key_readiness.ProviderKeyMissing) as raised:
            await key_readiness.assert_keys_available(
                effective,
                organization_id=7,
                allow_managed_fallback=False,
            )

        assert raised.value.sections == ["llm"]
        assert "language model" in str(raised.value)
        assert raised.value.reason == "provider_key_missing"


@pytest.mark.asyncio
class TestOptingIntoFallback:
    async def test_an_unfillable_slot_moves_onto_the_platform_key(self, empty_vault):
        effective = _byok_llm_with_no_inline_key()

        unresolved = await byok_resolution.apply(
            effective,
            organization_id=7,
            allow_managed_fallback=True,
        )

        # Still reported: the caller may want to log it even though the call
        # will now go through.
        assert unresolved == ["llm"]
        assert effective.llm.use_platform_key is True

    async def test_the_stamp_moves_with_it(self, empty_vault):
        """The one that costs money if it is wrong.

        ``key_source`` is what billing reads back off ``usage_info`` to decide
        whether a component was ours to charge for. A section failed over to
        our vendor key while still stamped "byok" runs at our expense and is
        never billed to anyone.
        """
        effective = _byok_llm_with_no_inline_key()

        await byok_resolution.apply(
            effective,
            organization_id=7,
            allow_managed_fallback=True,
        )

        assert effective.llm.key_source == "managed"

    async def test_the_gate_lets_the_call_through(self, empty_vault):
        """Nothing left to refuse -- resolution has already moved the slot."""
        effective = _byok_llm_with_no_inline_key()

        await key_readiness.assert_keys_available(
            effective,
            organization_id=7,
            allow_managed_fallback=True,
        )


@pytest.mark.asyncio
class TestSlotsTheCallNeverUses:
    """The regression that took inbound calling down in production.

    `byok_resolution.missing_keys` reports on every BYOK section it knows
    about, and that set includes `embeddings` — which a voice call never
    touches. An account with a leftover embeddings vendor and no key for it had
    every inbound call refused over a slot the call would not have used, on a
    pipeline that was configured correctly.
    """

    async def test_an_empty_embeddings_slot_does_not_refuse_a_voice_call(
        self, empty_vault
    ):
        effective = SimpleNamespace(
            llm=OpenAILLMService(api_key="sk-inline", model="gpt-4o"),
            stt=None,
            tts=None,
            realtime=None,
            # A vendor named, no key anywhere. Irrelevant to a voice call.
            embeddings=OpenAILLMService(api_key="", model="text-embedding-3-small"),
            is_realtime=False,
        )

        await byok_resolution.apply(effective, organization_id=7)
        await key_readiness.assert_keys_available(
            effective,
            organization_id=7,
            allow_managed_fallback=False,
        )

    async def test_an_empty_realtime_slot_does_not_refuse_a_pipeline_call(
        self, empty_vault
    ):
        """`realtime` is only reached when the agent is speech-to-speech."""
        effective = SimpleNamespace(
            llm=OpenAILLMService(api_key="sk-inline", model="gpt-4o"),
            stt=None,
            tts=None,
            realtime=OpenAILLMService(api_key="", model="gpt-realtime"),
            embeddings=None,
            is_realtime=False,
        )

        await byok_resolution.apply(effective, organization_id=7)
        await key_readiness.assert_keys_available(
            effective,
            organization_id=7,
            allow_managed_fallback=False,
        )

    async def test_a_realtime_agent_is_still_refused_on_its_realtime_slot(
        self, empty_vault
    ):
        """Narrowing the check must not turn it off for the agent that does
        use the slot."""
        effective = SimpleNamespace(
            llm=None,
            stt=None,
            tts=None,
            realtime=OpenAILLMService(api_key="", model="gpt-realtime"),
            embeddings=None,
            is_realtime=True,
        )

        await byok_resolution.apply(effective, organization_id=7)

        with pytest.raises(key_readiness.ProviderKeyMissing):
            await key_readiness.assert_keys_available(
                effective,
                organization_id=7,
                allow_managed_fallback=False,
            )


@pytest.mark.asyncio
class TestWhatTheGateDoesNotRefuse:
    async def test_a_slot_with_a_usable_key_passes(self, monkeypatch):
        async def _key(*_args, **_kwargs):
            return "sk-from-the-vault"

        monkeypatch.setattr(
            "api.services.configuration.organization_credentials.resolve_api_key",
            _key,
        )
        effective = _byok_llm_with_no_inline_key()

        await byok_resolution.apply(effective, organization_id=7)
        await key_readiness.assert_keys_available(
            effective,
            organization_id=7,
            allow_managed_fallback=False,
        )

        assert effective.llm.api_key == "sk-from-the-vault"
        assert effective.llm.key_source == "byok"

    async def test_no_organization_is_not_a_refusal(self, empty_vault):
        """The paths that cannot consult a vault at all -- refusing them here
        would break callers this gate is not about."""
        effective = _byok_llm_with_no_inline_key()

        await key_readiness.assert_keys_available(
            effective,
            organization_id=None,
            allow_managed_fallback=False,
        )
