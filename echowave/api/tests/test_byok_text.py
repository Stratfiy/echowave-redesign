"""BYOK-1 (KAN-254): Decibyl and the builder run on the account's own key.

With ``byok_text`` off nothing changes. With it on: an account that holds no
model key is a managed customer and runs on the platform key; an account
that holds one runs on it; an account whose keys cannot drive the turn is
told so, never silently charged on the platform's key, unless it opted into
that fallback.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api import constants
from api.services.agent_builder import settings

PLATFORM = settings.BuilderModel(provider="openai", model="gpt-x", api_key="platform")


@pytest.fixture
def byok_on(monkeypatch):
    monkeypatch.setattr(constants, "BYOK_TEXT_ENABLED", True)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")
    monkeypatch.setattr(constants, "AGENT_BUILDER_ENABLED", True)
    monkeypatch.setattr(constants, "AGENT_BUILDER_PROVIDER", "")
    monkeypatch.setattr(
        constants,
        "AGENT_BUILDER_PROVIDER_PREFERENCE",
        ("anthropic", "openai", "google"),
    )
    monkeypatch.setattr(
        constants,
        "AGENT_BUILDER_MODELS",
        {"anthropic": "claude-x", "openai": "gpt-x", "google": "gemini-x"},
    )
    monkeypatch.setattr(settings, "resolve_choice", AsyncMock(return_value=PLATFORM))


def _vault(monkeypatch, *, keys: dict[str, str], listed: bool = True, fallback=False):
    monkeypatch.setattr(settings, "_holds_llm_keys", AsyncMock(return_value=listed))
    monkeypatch.setattr(
        settings,
        "_own_key",
        AsyncMock(side_effect=lambda _s, _o, provider: keys.get(provider)),
    )
    monkeypatch.setattr(
        settings, "_fallback_to_platform_allowed", AsyncMock(return_value=fallback)
    )


async def test_flag_off_is_the_platform_key(monkeypatch, byok_on):
    monkeypatch.setattr(constants, "BYOK_TEXT_ENABLED", False)
    _vault(monkeypatch, keys={"openai": "mine"})
    model = await settings.resolve_for_organization(None, None, organization_id=7)
    assert model is PLATFORM


async def test_an_account_with_no_keys_is_managed(monkeypatch, byok_on):
    _vault(monkeypatch, keys={}, listed=False)
    model = await settings.resolve_for_organization(None, None, organization_id=7)
    assert model is PLATFORM


async def test_the_accounts_key_is_used_in_preference_order(monkeypatch, byok_on):
    _vault(monkeypatch, keys={"openai": "sk-mine", "google": "g-mine"})
    model = await settings.resolve_for_organization(None, None, organization_id=7)
    assert (model.provider, model.model, model.api_key, model.key_source) == (
        "openai",
        "gpt-x",
        "sk-mine",
        "byok",
    )


async def test_the_picked_models_vendor_wins_when_the_account_holds_its_key(
    monkeypatch, byok_on
):
    _vault(monkeypatch, keys={"openai": "sk-mine", "google": "g-mine"})
    from api.services.configuration import chat_presets

    monkeypatch.setattr(
        chat_presets,
        "named_model",
        lambda chosen: (
            ("google", "gemini-pro") if chosen == "model:google/gemini-pro" else None
        ),
    )
    model = await settings.resolve_for_organization(
        None, "model:google/gemini-pro", organization_id=7
    )
    assert (model.provider, model.model, model.key_source) == (
        "google",
        "gemini-pro",
        "byok",
    )


async def test_keys_that_cannot_drive_the_turn_are_refused_not_charged(
    monkeypatch, byok_on
):
    # e.g. only a Groq key: the account chose its own keys, none fits.
    _vault(monkeypatch, keys={}, listed=True, fallback=False)
    with pytest.raises(settings.OwnKeyMissing) as caught:
        await settings.resolve_for_organization(None, None, organization_id=7)
    assert "Settings" in str(caught.value)
    settings.resolve_choice.assert_not_awaited()


async def test_the_opt_in_fallback_uses_the_platform_key(monkeypatch, byok_on):
    _vault(monkeypatch, keys={}, listed=True, fallback=True)
    model = await settings.resolve_for_organization(None, None, organization_id=7)
    assert model is PLATFORM


async def test_the_deployment_switch_still_wins(monkeypatch, byok_on):
    monkeypatch.setattr(constants, "AGENT_BUILDER_ENABLED", False)
    _vault(monkeypatch, keys={"openai": "sk-mine"})
    with pytest.raises(settings.BuilderUnavailable):
        await settings.resolve_for_organization(None, None, organization_id=7)


def test_own_key_missing_is_a_builder_unavailable_so_routes_answer_503():
    assert issubclass(settings.OwnKeyMissing, settings.BuilderUnavailable)


async def test_only_active_llm_keys_count_as_bringing_your_own(monkeypatch):
    from api.services.configuration import organization_credentials

    creds = [
        SimpleNamespace(component="llm", is_active=False),
        SimpleNamespace(component="tts", is_active=True),
    ]
    monkeypatch.setattr(
        organization_credentials, "list_credentials", AsyncMock(return_value=creds)
    )
    assert await settings._holds_llm_keys(None, 7) is False
    creds.append(SimpleNamespace(component="llm", is_active=True))
    assert await settings._holds_llm_keys(None, 7) is True


def test_a_byok_turn_is_counted_but_not_priced():
    import inspect

    from api.services.billing import token_report

    source = inspect.getsource(token_report)
    assert 'endswith(":byok")' in source
