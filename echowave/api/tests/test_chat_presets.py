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
