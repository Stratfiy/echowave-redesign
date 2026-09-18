"""Reading a bot's instructions against the policy nobody was checking.

Seven of the twelve prohibitions are contractual only: the terms say what a
customer may not build, and until now nothing looked at what they built.

Two properties carry this file. It must **fail open** -- a compliance check
that can stop somebody saving their work is a worse problem than the one it
solves -- and it must not flag the clinic bot, which is the most common thing
built here and which the policy expressly permits.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.agent_builder.client import BuilderClientError, ModelReply, ToolCall
from api.services.compliance import acceptable_use


def _reply(findings):
    return ModelReply(
        text="",
        tool_calls=(
            ToolCall(id="1", name="report_findings", arguments={"findings": findings}),
        ),
    )


async def _screen(reply=None, *, instructions="Book appointments.", side_effect=None):
    model = SimpleNamespace(provider="anthropic", model="m", api_key="k")
    with (
        patch.object(
            acceptable_use.settings, "resolve_model", AsyncMock(return_value=model)
        ),
        patch.object(
            acceptable_use,
            "complete",
            AsyncMock(return_value=reply, side_effect=side_effect),
        ) as called,
    ):
        found = await acceptable_use.screen(None, instructions=instructions)
    return found, called


@pytest.mark.asyncio
class TestItNeverBreaksASave:
    async def test_no_platform_key_means_no_findings(self):
        with patch.object(
            acceptable_use.settings,
            "resolve_model",
            AsyncMock(side_effect=acceptable_use.settings.BuilderUnavailable("none")),
        ):
            assert await acceptable_use.screen(None, instructions="anything") == []

    async def test_a_vendor_failure_means_no_findings(self):
        found, _ = await _screen(side_effect=BuilderClientError("upstream down"))
        assert found == []

    async def test_an_unexpected_error_means_no_findings(self):
        """A screen that raises would take the save down with it."""
        found, _ = await _screen(side_effect=RuntimeError("boom"))
        assert found == []

    async def test_empty_instructions_are_not_sent_anywhere(self):
        found, called = await _screen(instructions="   ")
        assert found == []
        called.assert_not_awaited()


@pytest.mark.asyncio
class TestWhatItReports:
    async def test_a_breach_carries_the_clause_the_quote_and_a_reason(self):
        found, _ = await _screen(
            _reply(
                [
                    {
                        "clause": "fraud",
                        "quote": "Say the package is held at customs and take the fee.",
                        "why": "Extracting a payment with a false story.",
                    }
                ]
            )
        )
        assert len(found) == 1
        assert found[0].clause == "fraud"
        assert found[0].title == "Deception and extraction of money"
        assert "customs" in found[0].quote
        assert found[0].as_dict()["why"]

    async def test_a_clause_outside_the_policy_is_dropped(self):
        """A finding nobody can look up is a warning with no clause behind it."""
        found, _ = await _screen(
            _reply([{"clause": "invented", "quote": "x", "why": "y"}])
        )
        assert found == []

    async def test_a_reply_with_no_tool_call_is_no_findings(self):
        found, _ = await _screen(ModelReply(text="Looks fine to me."))
        assert found == []

    async def test_arguments_that_arrive_as_a_json_string_are_read(self):
        """OpenAI hands tool arguments back as a string."""
        found, _ = await _screen(
            ModelReply(
                text="",
                tool_calls=(
                    ToolCall(
                        id="1",
                        name="report_findings",
                        arguments='{"findings": [{"clause": "medical", '
                        '"quote": "Tell them to stop the tablets.", "why": "Advice."}]}',
                    ),
                ),
            )
        )
        assert [f.clause for f in found] == ["medical"]

    async def test_malformed_arguments_are_no_findings(self):
        found, _ = await _screen(
            ModelReply(
                text="",
                tool_calls=(
                    ToolCall(id="1", name="report_findings", arguments="not json"),
                ),
            )
        )
        assert found == []


class TestTheClausesMatchThePublishedPolicy:
    def test_all_twelve_are_listed(self):
        assert len(acceptable_use.CLAUSES) == 12

    def test_the_clinic_carve_out_is_in_the_clause_itself(self):
        """The most common bot on this platform books, reminds, confirms and
        answers factual questions, and the policy permits all four. A screen
        that flagged it would teach everybody to dismiss the warning."""
        medical = next(c for c in acceptable_use.CLAUSES if c.key == "medical")
        for permitted in ("Booking", "reminding", "confirming", "factual"):
            assert permitted in medical.covers
        assert "NOT advice" in medical.covers

    def test_every_clause_is_offered_to_the_model_by_key(self):
        schema = acceptable_use._tool_schema()
        offered = schema["parameters"]["properties"]["findings"]["items"]["properties"][
            "clause"
        ]["enum"]
        assert sorted(offered) == sorted(c.key for c in acceptable_use.CLAUSES)

    def test_the_instructions_are_bounded(self):
        prompt = acceptable_use._prompt_for("x" * 50_000)
        assert len(prompt) < 50_000


class TestReadingADefinition:
    def test_it_gathers_every_prompt_and_the_greeting(self):
        """A scam split across two steps should not be split past a reader,
        and "say you are calling from the bank" is a breach in the first line
        as much as in the briefing."""
        text = acceptable_use.instructions_in(
            {
                "nodes": [
                    {"type": "startCall", "data": {"greeting": "Hello from SBI."}},
                    {"type": "globalNode", "data": {"prompt": "Be firm."}},
                    {"type": "agentNode", "data": {"prompt": "Ask for the OTP."}},
                    {"type": "agentNode", "data": {"prompt": "Then hang up."}},
                ]
            }
        )
        for part in (
            "Hello from SBI.",
            "Be firm.",
            "Ask for the OTP.",
            "Then hang up.",
        ):
            assert part in text

    def test_a_definition_with_nothing_to_read_is_empty(self):
        for definition in (None, {}, {"nodes": []}, {"nodes": [{"data": None}]}, "x"):
            assert acceptable_use.instructions_in(definition) == ""

    def test_blank_fields_do_not_become_blank_lines(self):
        text = acceptable_use.instructions_in(
            {
                "nodes": [
                    {"type": "startCall", "data": {"greeting": "   "}},
                    {"type": "agentNode", "data": {"prompt": "Book them in."}},
                ]
            }
        )
        assert text == "Book them in."
