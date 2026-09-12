"""The chat asks how an agent should sound, and then actually sets it.

The trap this file exists for: the builder's prompt could be told to ask which
voice and which brain, take an answer, and have no tool to apply it. The chat
would look right, the user would believe they chose, and the first caller
would hear the default -- with nothing anywhere saying why. Correct-looking
behaviour with nothing behind it is the failure this codebase keeps having.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.services.agent_builder import tools as builder_tools
from api.services.agent_builder.session import SYSTEM_PROMPT
from api.services.configuration import agent_options


class TestTheToolsExist:
    def test_both_are_offered(self):
        names = [tool["name"] for tool in builder_tools.tool_schemas()]
        assert "list_voice_and_brain" in names
        assert "set_voice_and_brain" in names

    def test_listing_comes_before_setting(self):
        # Order in the catalogue is a nudge the model reads, and a brain slug
        # written from memory is a stack that resolves to nothing at call time.
        names = [tool["name"] for tool in builder_tools.tool_schemas()]
        assert names.index("list_voice_and_brain") < names.index("set_voice_and_brain")

    def test_setting_requires_the_agent_id(self):
        schema = next(
            t
            for t in builder_tools.tool_schemas()
            if t["name"] == "set_voice_and_brain"
        )
        assert schema["parameters"]["required"] == ["workflow_id"]

    def test_the_prompt_tells_it_to_ask_and_to_apply(self):
        # Both halves. A prompt that says "ask" without "then set" is the bug
        # this file is about.
        assert "list_voice_and_brain" in SYSTEM_PROMPT
        assert "set_voice_and_brain" in SYSTEM_PROMPT

    def test_the_prompt_still_forbids_vendor_talk(self):
        assert "never be asked" in SYSTEM_PROMPT
        assert "Never name a vendor or a model" in SYSTEM_PROMPT


class TestWhatTheChatIsGiven:
    @pytest.mark.asyncio
    async def test_every_brain_comes_back_with_a_label_and_a_price(self):
        with patch.object(
            agent_options, "price_per_minute", AsyncMock(return_value=250)
        ):
            result = await builder_tools._list_voice_and_brain(None, organization_id=42)
        assert result["brains"]
        for brain in result["brains"]:
            assert brain["label"]
            assert brain["what_it_is_for"]
            assert brain["rupees_per_minute"] == 2.5

    @pytest.mark.asyncio
    async def test_an_unpriceable_tier_says_none_rather_than_zero(self):
        # "We cannot price this yet" and "this is free" read the same in a
        # number and differently in a sentence.
        with patch.object(
            agent_options, "price_per_minute", AsyncMock(return_value=None)
        ):
            result = await builder_tools._list_voice_and_brain(None, organization_id=42)
        assert result["brains"][0]["rupees_per_minute"] is None

    @pytest.mark.asyncio
    async def test_voices_carry_a_name_and_gender_not_a_vendor(self):
        with patch.object(
            agent_options, "price_per_minute", AsyncMock(return_value=250)
        ):
            result = await builder_tools._list_voice_and_brain(None, organization_id=42)
        assert result["voices"]
        for voice in result["voices"]:
            assert voice["name"]
            assert "provider" not in voice


def _workflow(configurations=None):
    return SimpleNamespace(
        id=7, name="Front desk", workflow_configurations=configurations or {}
    )


class TestSetting:
    @pytest.mark.asyncio
    async def test_it_saves_to_the_draft_and_says_it_is_not_live(self):
        saved = {}

        async def _save(workflow_id, **kwargs):
            saved["id"] = workflow_id
            saved.update(kwargs)

        with (
            patch.object(
                db_client, "get_workflow", AsyncMock(return_value=_workflow())
            ),
            patch.object(
                db_client, "save_workflow_draft", AsyncMock(side_effect=_save)
            ),
        ):
            result = await builder_tools._set_voice_and_brain(
                organization_id=42, workflow_id=7, brain="accurate"
            )

        assert result["set"] is True
        assert result["live"] is False
        assert saved["id"] == 7
        assert saved["workflow_configurations"]

    @pytest.mark.asyncio
    async def test_an_invented_brain_is_refused_with_the_real_ones(self):
        # A tier the model made up would be written into the stack and resolve
        # to nothing at call time.
        result = await builder_tools._set_voice_and_brain(
            organization_id=42, workflow_id=7, brain="galaxy-max"
        )
        assert "not a brain" in result["error"]
        assert "accurate" in result["error"]

    @pytest.mark.asyncio
    async def test_an_invented_voice_is_refused(self):
        result = await builder_tools._set_voice_and_brain(
            organization_id=42, workflow_id=7, voice="sir-david"
        )
        assert "not a voice" in result["error"]

    @pytest.mark.asyncio
    async def test_setting_only_the_brain_does_not_reset_the_voice(self):
        # "Make it smarter" is not a request to change the voice back to
        # default, and a chat message is always a partial answer.
        existing_voice = agent_options.voices()[-1].voice_id
        base = agent_options.managed_stack_override(
            voice=existing_voice, llm_tier="lite"
        )
        saved = {}

        async def _save(workflow_id, **kwargs):
            saved.update(kwargs)

        with (
            patch.object(
                db_client, "get_workflow", AsyncMock(return_value=_workflow(base))
            ),
            patch.object(
                db_client, "save_workflow_draft", AsyncMock(side_effect=_save)
            ),
        ):
            await builder_tools._set_voice_and_brain(
                organization_id=42, workflow_id=7, brain="accurate"
            )

        written = str(saved["workflow_configurations"])
        assert existing_voice in written
        assert "accurate" in written

    @pytest.mark.asyncio
    async def test_nothing_chosen_is_an_error_not_a_silent_no_op(self):
        result = await builder_tools._set_voice_and_brain(
            organization_id=42, workflow_id=7
        )
        assert "Nothing to set" in result["error"]

    @pytest.mark.asyncio
    async def test_another_accounts_agent_is_not_found(self):
        # The id came from a model. That is request-supplied data however it
        # came by it, so the read is org-scoped.
        with patch.object(db_client, "get_workflow", AsyncMock(return_value=None)):
            result = await builder_tools._set_voice_and_brain(
                organization_id=42, workflow_id=999, brain="lite"
            )
        assert "No agent 999" in result["error"]

    @pytest.mark.asyncio
    async def test_a_non_numeric_id_is_refused_before_any_query(self):
        result = await builder_tools._set_voice_and_brain(
            organization_id=42, workflow_id="seven", brain="lite"
        )
        assert "must be the number" in result["error"]

    @pytest.mark.asyncio
    async def test_a_save_failure_comes_back_as_an_error_not_an_exception(self):
        with (
            patch.object(
                db_client, "get_workflow", AsyncMock(return_value=_workflow())
            ),
            patch.object(
                db_client,
                "save_workflow_draft",
                AsyncMock(side_effect=RuntimeError("disk on fire")),
            ),
        ):
            result = await builder_tools._set_voice_and_brain(
                organization_id=42, workflow_id=7, brain="lite"
            )
        assert "disk on fire" in result["error"]


class TestDispatch:
    @pytest.mark.asyncio
    async def test_both_names_route(self):
        with patch.object(
            agent_options, "price_per_minute", AsyncMock(return_value=100)
        ):
            listed = await builder_tools.dispatch(
                "list_voice_and_brain", {}, session=None, organization_id=42, user_id=1
            )
        assert "brains" in listed

        setting = await builder_tools.dispatch(
            "set_voice_and_brain",
            {"workflow_id": 7, "brain": "nope"},
            session=None,
            organization_id=42,
            user_id=1,
        )
        assert "error" in setting
