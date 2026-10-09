"""A person can change what their agent says first, from the chat.

Seen in an audit of main in October 2026: "change the greeting to say
Sharma Dental" failed three ways. The whole-step tool had no way to touch a
greeting. Find-and-replace changed a greeting only when the same text was
also in some prompt, and otherwise answered that the text "does not appear
in any step" -- about text the caller hears on every call. And the card's
diff showed prompts only, so a greeting that did change was invisible on
the thing a person approves.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import self_edit

DEFINITION = {
    "nodes": [
        {
            "id": "s",
            "type": "startCall",
            "data": {
                "name": "Start",
                "prompt": "Ask how you can help.",
                "greeting_type": "text",
                "greeting": "Namaste, this is Asha from City Dental.",
            },
        },
        {
            "id": "g",
            "type": "globalNode",
            "data": {"name": "Global", "prompt": "Be polite."},
        },
        {
            "id": "n1",
            "type": "agentNode",
            "data": {"name": "Book", "prompt": "Find a slot."},
        },
    ]
}


async def _propose(arguments, *, definition=DEFINITION):
    workflow = SimpleNamespace(
        id=3, name="Front desk", workflow_definition=copy.deepcopy(definition)
    )
    saved = AsyncMock(return_value=SimpleNamespace(version_number=4))
    with (
        patch.object(
            self_edit.db_client, "get_workflow_by_id", AsyncMock(return_value=workflow)
        ),
        patch.object(self_edit.db_client, "save_workflow_draft", saved),
        patch.object(self_edit.agent_timeline, "record", AsyncMock()) as record,
    ):
        result = await self_edit.propose(
            organization_id=7, workflow_id=3, workflow_run_id=11, arguments=arguments
        )
    return result, saved, record


def _node(saved, node_id):
    definition = saved.await_args.kwargs["workflow_definition"]
    return next(n for n in definition["nodes"] if n["id"] == node_id)


class TestTheBotCanSeeItsGreeting:
    def test_the_steps_block_shows_the_greeting(self):
        block = self_edit.steps_block(DEFINITION)
        assert "### Start\nAsk how you can help.\nGreeting: Namaste" in block
        assert "Greeting" not in block.split("### Book")[1]

    def test_a_start_step_with_only_a_greeting_is_a_step(self):
        only_greeting = {
            "nodes": [
                {
                    "id": "s",
                    "type": "startCall",
                    "data": {"name": "Start", "greeting": "Hello!"},
                }
            ]
        }
        assert self_edit.find_step(only_greeting, "start")["id"] == "s"
        assert "Greeting: Hello!" in self_edit.steps_block(only_greeting)

    def test_the_tool_offers_new_greeting(self):
        assert "new_greeting" in self_edit.tool_properties()
        assert "greeting" in self_edit.DESCRIPTION


@pytest.mark.asyncio
class TestFindAndReplace:
    async def test_text_only_in_the_greeting_is_found_and_changed(self):
        result, saved, record = await _propose(
            {"find": "City Dental", "replace_with": "Sharma Dental", "why": "rename"}
        )
        assert result["status"] == "proposed", result
        start = _node(saved, "s")
        assert start["data"]["greeting"] == "Namaste, this is Asha from Sharma Dental."
        assert start["data"]["prompt"] == "Ask how you can help."  # untouched
        payload = record.await_args.kwargs["payload"]
        assert payload["step"] == "Start"
        assert payload["greetings"] == [
            {
                "step": "Start",
                "node_id": "s",
                "old": "Namaste, this is Asha from City Dental.",
                "new": "Namaste, this is Asha from Sharma Dental.",
            }
        ]
        assert payload["diff"] == ""  # no prompt moved
        assert "greeting" in result["note"]

    async def test_a_name_in_a_prompt_and_a_greeting_changes_in_both(self):
        definition = copy.deepcopy(DEFINITION)
        definition["nodes"][2]["data"]["prompt"] = "Book at City Dental."
        result, saved, record = await _propose(
            {"find": "City Dental", "replace_with": "Sharma Dental", "why": ""},
            definition=definition,
        )
        assert result["status"] == "proposed"
        assert _node(saved, "n1")["data"]["prompt"] == "Book at Sharma Dental."
        assert "Sharma Dental" in _node(saved, "s")["data"]["greeting"]
        payload = record.await_args.kwargs["payload"]
        assert payload["steps"] == ["Start", "Book"]
        assert len(payload["greetings"]) == 1
        assert "+Book at Sharma Dental." in payload["diff"]

    async def test_text_nowhere_says_steps_and_greetings(self):
        result, saved, _ = await _propose(
            {"find": "Apollo", "replace_with": "Sharma", "why": ""}
        )
        assert result["status"] == "not_proposed"
        assert "does not appear in any step or greeting" in result["reason"]
        saved.assert_not_awaited()

    async def test_a_recorded_greeting_is_not_text_to_change(self):
        definition = copy.deepcopy(DEFINITION)
        definition["nodes"][0]["data"]["greeting_type"] = "audio"
        result, saved, _ = await _propose(
            {"find": "City Dental", "replace_with": "Sharma Dental", "why": ""},
            definition=definition,
        )
        assert result["status"] == "not_proposed"
        saved.assert_not_awaited()


@pytest.mark.asyncio
class TestANewGreeting:
    async def test_a_step_can_be_given_a_new_greeting(self):
        result, saved, record = await _propose(
            {
                "step": "Start",
                "new_greeting": "Hello, Sharma Dental, Asha speaking.",
                "why": "new greeting",
            }
        )
        assert result["status"] == "proposed", result
        start = _node(saved, "s")
        assert start["data"]["greeting"] == "Hello, Sharma Dental, Asha speaking."
        assert start["data"]["prompt"] == "Ask how you can help."
        kwargs = record.await_args.kwargs
        assert (
            kwargs["summary"] == "Proposed a change to Start's greeting: new greeting"
        )
        payload = kwargs["payload"]
        assert payload["greetings"][0]["new"] == "Hello, Sharma Dental, Asha speaking."
        assert payload["diff"] == "" and payload["old"] == "" and payload["new"] == ""

    async def test_a_prompt_and_a_greeting_together(self):
        result, saved, record = await _propose(
            {
                "step": "Start",
                "new_prompt": "Ask for their name, then how you can help.",
                "new_greeting": "Hi! Sharma Dental.",
                "why": "",
            }
        )
        assert result["status"] == "proposed"
        start = _node(saved, "s")
        assert start["data"]["greeting"] == "Hi! Sharma Dental."
        assert start["data"]["prompt"].startswith("Ask for their name")
        payload = record.await_args.kwargs["payload"]
        assert "+Ask for their name" in payload["diff"]
        assert payload["greetings"][0]["new"] == "Hi! Sharma Dental."

    async def test_a_step_with_no_greeting_is_refused_with_the_ones_that_have(self):
        result, saved, _ = await _propose(
            {"step": "Book", "new_greeting": "Hello", "why": ""}
        )
        assert result["status"] == "not_proposed"
        assert "Book has no greeting" in result["reason"]
        assert "Start" in result["reason"]
        saved.assert_not_awaited()

    async def test_the_same_greeting_is_not_a_change(self):
        result, saved, _ = await _propose(
            {
                "step": "Start",
                "new_greeting": "Namaste, this is Asha from City Dental.",
                "why": "",
            }
        )
        assert result["status"] == "not_proposed"
        saved.assert_not_awaited()

    async def test_the_rewrite_guard_still_holds_with_a_greeting(self):
        definition = copy.deepcopy(DEFINITION)
        definition["nodes"][0]["data"]["prompt"] = "Rule. " * 200  # 1200 chars
        result, saved, _ = await _propose(
            {
                "step": "Start",
                "new_prompt": "One line.",
                "new_greeting": "Hello there.",
                "why": "",
            },
            definition=definition,
        )
        assert result["status"] == "not_proposed"
        assert "find and replace_with" in result["reason"]
        saved.assert_not_awaited()
