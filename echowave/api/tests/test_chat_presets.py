"""Four brains for a chat, chosen per message and honoured by the run.

Arrival tests for the picker: the choice rides the message, lands in the
run's session data, and moves only the LLM slot of the run's stack.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.services.configuration import chat_presets, managed_tiers
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY as KEY,
)


class TestThePresets:
    def test_every_preset_is_a_managed_tier_that_resolves(self):
        for preset in chat_presets.CHAT_PRESETS:
            assert preset.llm_tier in managed_tiers.LLM_TIERS, preset.slug
            assert managed_tiers.resolve("llm", preset.llm_tier).model

    def test_nothing_chosen_is_none_and_nonsense_is_refused(self):
        assert chat_presets.normalise(None) is None
        assert chat_presets.normalise("  ") is None
        assert chat_presets.normalise(" Deep ") == "deep"
        with pytest.raises(chat_presets.UnknownPreset):
            chat_presets.normalise("galaxy")


class TestApplyingOne:
    def test_only_the_brain_moves_on_a_hand_built_stack(self):
        configs = {
            "other": 1,
            KEY: {
                "version": 3,
                "stack": {
                    "architecture": "pipeline",
                    "llm": {"provider": "openai", "model": "gpt-4.1", "api_key": "k"},
                    "tts": {"provider": "elevenlabs", "model": "x", "voice": "v"},
                },
            },
        }
        out = chat_presets.apply(configs, "advanced")
        assert out["other"] == 1
        assert out[KEY]["stack"]["llm"] == {
            "provider": "decibyl",
            "model": "advanced",
            "api_key": "",
        }
        assert out[KEY]["stack"]["tts"] == configs[KEY]["stack"]["tts"]
        # The caller's dict is not mutated: the pinned definition stays pinned.
        assert configs[KEY]["stack"]["llm"]["provider"] == "openai"

    def test_a_bot_on_the_default_gets_a_managed_stack_with_that_brain(self):
        out = chat_presets.apply({}, "deep")
        assert out[KEY]["stack"]["llm"]["model"] == "accurate"
        assert out[KEY]["stack"]["llm"]["provider"] == "decibyl"

    def test_nothing_chosen_changes_nothing(self):
        configs = {"a": 1}
        assert chat_presets.apply(configs, None) == configs
        assert chat_presets.apply(None, "") == {}


@pytest.mark.asyncio
class TestItRidesTheMessage:
    async def _post(self, body):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        workflow = SimpleNamespace(id=3, name="Front desk", folder_id=5)
        try:
            with (
                patch(
                    "api.routes.agent_timeline.db_client.get_workflow",
                    new=AsyncMock(return_value=workflow),
                ),
                patch(
                    "api.routes.agent_timeline.agent_timeline.record", new=AsyncMock()
                ) as record,
                patch(
                    "api.routes.agent_timeline.enqueue_job", new=AsyncMock()
                ) as enqueue,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post("/api/v1/timeline/message", json=body)
        finally:
            app.dependency_overrides.pop(get_user, None)
        return response, record, enqueue

    async def test_the_choice_reaches_the_job_and_the_row(self):
        response, record, enqueue = await self._post(
            {"workflow_id": 3, "text": "Think hard about this", "preset": "deep"}
        )
        assert response.status_code == 200, response.text
        assert enqueue.await_args.args[1:] == (3, None, "Think hard about this", "deep")
        assert record.await_args.kwargs["payload"]["preset"] == "deep"

    async def test_no_choice_is_the_bots_own(self):
        response, _, enqueue = await self._post({"workflow_id": 3, "text": "hi"})
        assert response.status_code == 200, response.text
        assert enqueue.await_args.args[-1] is None

    async def test_a_made_up_brain_is_refused(self):
        response, record, _ = await self._post(
            {"workflow_id": 3, "text": "hi", "preset": "galaxy"}
        )
        assert response.status_code == 422
        assert not record.await_count


@pytest.mark.asyncio
class TestTheRunCarriesIt:
    async def test_the_session_is_created_with_the_preset(self):
        """The reply path writes the choice where the runner reads it."""
        from api.services.workflow import channel_reply

        workflow = SimpleNamespace(id=3, name="Front desk", organization_id=7)
        ensured = AsyncMock(side_effect=RuntimeError("stop here"))
        with (
            patch(
                "api.services.workflow.channel_reply.db_client.get_workflow_by_id",
                new=AsyncMock(return_value=workflow),
            ),
            patch(
                "api.services.workflow.channel_reply.db_client.create_workflow_run",
                new=AsyncMock(return_value=SimpleNamespace(id=11)),
            ),
            patch(
                "api.services.workflow.channel_reply.authorize_workflow_run_start",
                new=AsyncMock(
                    return_value=SimpleNamespace(has_quota=True, error_message=None)
                ),
            ),
            patch(
                "api.services.workflow.channel_reply.db_client.ensure_workflow_run_text_session",
                new=ensured,
            ),
            patch(
                "api.services.workflow.channel_reply.agent_timeline.record",
                new=AsyncMock(),
            ),
        ):
            try:
                await channel_reply.answer_in_channel(3, None, "hi", preset="advanced")
            except RuntimeError:
                pass
        assert (
            ensured.await_args.kwargs["session_data"][chat_presets.SESSION_KEY]
            == "advanced"
        )


class TestMoreModels:
    def test_a_catalogue_model_is_a_choice_and_a_made_up_one_is_not(self):
        assert chat_presets.normalise("model:openai/gpt-5") == "model:openai/gpt-5"
        assert chat_presets.normalise(" Model:OpenAI/gpt-5 ") == "model:openai/gpt-5"
        with pytest.raises(chat_presets.UnknownPreset):
            chat_presets.normalise("model:openai/gpt-9")
        with pytest.raises(chat_presets.UnknownPreset):
            chat_presets.normalise("model:acme/gpt-5")

    def test_advanced_is_off_the_menu_but_still_answers(self):
        assert [p.slug for p in chat_presets.CHAT_PRESETS] == [
            "everyday",
            "smart",
            "deep",
        ]
        assert chat_presets.normalise("advanced") == "advanced"
        assert (
            chat_presets.apply({}, "advanced")[KEY]["stack"]["llm"]["model"]
            == "advanced"
        )

    def test_a_named_model_pins_that_vendor_on_our_key(self):
        out = chat_presets.apply({}, "model:anthropic/claude-sonnet-5")
        assert out[KEY]["stack"]["llm"] == {
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "api_key": "",
            "use_platform_key": True,
        }
        # The rest of the managed stack is still there for the run.
        assert out[KEY]["stack"]["stt"]["provider"] == "decibyl"

    def test_a_named_model_on_a_hand_built_stack_moves_only_the_brain(self):
        configs = {
            KEY: {
                "version": 3,
                "stack": {
                    "architecture": "pipeline",
                    "llm": {"provider": "openai", "model": "gpt-4.1", "api_key": "k"},
                    "tts": {"provider": "elevenlabs", "model": "x", "voice": "v"},
                },
            }
        }
        out = chat_presets.apply(configs, "model:google/gemini-3.5-flash")
        assert out[KEY]["stack"]["llm"]["provider"] == "google"
        assert out[KEY]["stack"]["llm"]["use_platform_key"] is True
        assert out[KEY]["stack"]["tts"] == configs[KEY]["stack"]["tts"]

    def test_labels_for_the_button(self):
        assert chat_presets.model_label("deep") == "Deep"
        assert chat_presets.model_label("model:openai/gpt-5") == "GPT-5"
        assert chat_presets.model_label("") is None

    @pytest.mark.asyncio
    async def test_the_menu_offers_only_vendors_we_hold_a_key_for(self):
        with patch(
            "api.services.configuration.platform_credentials.managed_providers",
            new=AsyncMock(
                return_value={"llm": ["openai", "sarvam"], "tts": ["sarvam"]}
            ),
        ):
            menu = await chat_presets.menu(object())
            narrowed = await chat_presets.menu(
                object(), vendors=["openai", "anthropic"]
            )
        assert [p["slug"] for p in menu["presets"]] == ["everyday", "smart", "deep"]
        assert [v["id"] for v in menu["vendors"]] == ["openai", "sarvam"]
        assert menu["vendors"][0]["models"][0] == {
            "slug": "model:openai/gpt-5",
            "label": "GPT-5",
        }
        assert [v["id"] for v in narrowed["vendors"]] == ["openai"]


@pytest.mark.asyncio
class TestDecibylHonoursTheChoice:
    async def test_a_preset_runs_on_its_tier_when_the_builder_can_drive_it(self):
        from api.services.agent_builder import settings

        with (
            patch.dict("os.environ", {"MANAGED_LLM_ACCURATE": "openai:gpt-4.1"}),
            patch(
                "api.services.agent_builder.settings.platform_credentials.resolve_api_key",
                new=AsyncMock(return_value="sk"),
            ),
        ):
            model = await settings.resolve_choice(object(), "deep")
        assert (model.provider, model.model, model.api_key) == (
            "openai",
            "gpt-4.1",
            "sk",
        )

    async def test_a_named_model_runs_as_named(self):
        from api.services.agent_builder import settings

        with patch(
            "api.services.agent_builder.settings.platform_credentials.resolve_api_key",
            new=AsyncMock(return_value="sk"),
        ):
            model = await settings.resolve_choice(
                object(), "model:anthropic/claude-opus-5"
            )
        assert (model.provider, model.model) == ("anthropic", "claude-opus-5")

    async def test_a_vendor_the_builder_cannot_drive_falls_back(self):
        from api.services.agent_builder import settings

        fallback = settings.BuilderModel("openai", "gpt-4.1", "sk")
        with (
            patch(
                "api.services.agent_builder.settings.resolve_model",
                new=AsyncMock(return_value=fallback),
            ),
            patch(
                "api.services.agent_builder.settings.platform_credentials.resolve_api_key",
                new=AsyncMock(return_value="sk"),
            ),
        ):
            assert (
                await settings.resolve_choice(object(), "model:sarvam/sarvam-105b")
                is fallback
            )
            assert await settings.resolve_choice(object(), None) is fallback

    async def test_the_route_menu_narrows_to_the_builders_vendors_for_decibyl(self):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        try:
            with (
                patch("api.routes.agent_timeline.db_client.async_session") as session,
                patch(
                    "api.services.configuration.platform_credentials.managed_providers",
                    new=AsyncMock(return_value={"llm": ["openai", "sarvam"]}),
                ),
            ):
                session.return_value.__aenter__.return_value = object()
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    for_bots = await client.get("/api/v1/timeline/brains")
                    for_decibyl = await client.get(
                        "/api/v1/timeline/brains?assistant=true"
                    )
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert for_bots.status_code == 200, for_bots.text
        assert [v["id"] for v in for_bots.json()["vendors"]] == ["openai", "sarvam"]
        assert [v["id"] for v in for_decibyl.json()["vendors"]] == ["openai"]

    async def test_the_meter_route_refuses_another_tenants_bot(self):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        usage = chat_memory_usage = SimpleNamespace(
            as_dict=lambda: {
                "used_tokens": 5,
                "budget_tokens": 8_000,
                "messages_kept": 1,
                "messages_total": 1,
                "plan_code": "free",
                "raise_to": "everyday",
            }
        )
        del chat_memory_usage
        try:
            with (
                patch(
                    "api.routes.agent_timeline.db_client.get_workflow",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "api.routes.agent_timeline.chat_memory.usage",
                    new=AsyncMock(return_value=usage),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    missing = await client.get("/api/v1/timeline/memory?workflow_id=99")
                    mine = await client.get("/api/v1/timeline/memory?assistant=true")
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert missing.status_code == 404
        assert mine.status_code == 200, mine.text
        assert mine.json()["budget_tokens"] == 8_000
