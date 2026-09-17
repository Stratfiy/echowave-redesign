"""A bot may send when somebody asked it to, and not when nobody is there.

A bot's connected-app tools execute: no card, no confirmation, nothing
between the model choosing GMAIL_SEND_EMAIL and the mail leaving. On a call
that is right -- the customer asked for the booking out loud a second ago
and is still on the line when it happens. The conversation is the
supervision.

A routine is the other case. It fires at eight because a schedule said so,
and acts with nobody there.

The first cut of this attached reads only to every generated bot, which was
true of routines and wrong about everything else: a bot that can only read
is half of what anybody builds one for, and "attach the send by hand in the
builder" is the friction that describing a bot exists to remove. So the line
moved to where it belongs -- not which bot, not which tool, but whether this
particular run has a person in it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import unattended


class TestReadingTheSetting:
    def test_off_when_nobody_set_it(self):
        assert unattended.writes_allowed({}) is False
        assert unattended.writes_allowed(None) is False

    def test_on_only_for_a_real_true(self):
        assert unattended.writes_allowed({unattended.CONFIG_KEY: True}) is True

    def test_a_half_finished_edit_is_not_consent(self):
        """A string, a number or a null in that key is somebody mid-edit, not
        permission to send mail at 8am."""
        for value in ("true", "yes", 1, None, [], {}):
            assert unattended.writes_allowed({unattended.CONFIG_KEY: value}) is False


class TestTellingTheRunsApart:
    def test_a_routine_run_is_unattended(self):
        assert unattended.is_unattended({"routine": {"id": 1, "name": "Brief"}}) is True

    def test_a_call_is_not(self):
        assert unattended.is_unattended({}) is False
        assert unattended.is_unattended(None) is False
        assert unattended.is_unattended({"campaign": {"id": 2}}) is False

    @pytest.mark.asyncio
    async def test_no_run_id_is_not_unattended(self):
        assert await unattended.run_is_unattended(None) is False

    @pytest.mark.asyncio
    async def test_a_lookup_that_fails_does_not_strip_a_live_call(self):
        """The unsafe direction, on purpose: a database blip must not quietly
        take a bot's tools away mid-conversation. Logged, not silent."""
        from api.db import db_client

        with patch.object(
            db_client,
            "get_workflow_run",
            AsyncMock(side_effect=RuntimeError("db down")),
        ):
            assert await unattended.run_is_unattended(7) is False


def _tool(slug: str, *, read: bool):
    return SimpleNamespace(
        tool_uuid=f"u-{slug.lower()}",
        name=slug,
        category="composio",
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": "gmail"},
        },
    )


READ = _tool("GMAIL_FETCH_EMAILS", read=True)
WRITE = _tool("GMAIL_SEND_EMAIL", read=False)
NOT_COMPOSIO = SimpleNamespace(
    tool_uuid="u-transfer", name="Transfer", category="transfer_call", definition={}
)


def _manager(*, gated: bool):
    from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

    manager = CustomToolManager(SimpleNamespace(_workflow_run_id=1))
    manager._writes_gated = gated
    return manager


class TestWhatTheModelIsOffered:
    @pytest.mark.asyncio
    async def test_a_write_is_withheld_on_an_unattended_run(self):
        """Withheld rather than refused later: a tool offered and then
        rejected spends a round and reads like a failure to retry."""
        kept = await _manager(gated=True)._minus_ungated_writes([READ, WRITE])
        assert kept == [READ]

    @pytest.mark.asyncio
    async def test_reads_are_never_withheld(self):
        kept = await _manager(gated=True)._minus_ungated_writes([READ])
        assert kept == [READ]

    @pytest.mark.asyncio
    async def test_everything_stays_when_a_person_is_there(self):
        kept = await _manager(gated=False)._minus_ungated_writes([READ, WRITE])
        assert kept == [READ, WRITE]

    @pytest.mark.asyncio
    async def test_a_transfer_is_not_a_connected_app_write(self):
        """The gate is about somebody else's SaaS. Ending or transferring a
        call is the bot doing its own job."""
        kept = await _manager(gated=True)._minus_ungated_writes([NOT_COMPOSIO])
        assert kept == [NOT_COMPOSIO]

    @pytest.mark.asyncio
    async def test_a_list_with_no_writes_is_not_even_checked(self):
        """The cheap path: no write in the list means no question to ask, so
        a bot with only reads never pays for the lookup."""
        manager = _manager(gated=True)
        manager._writes_gated = None
        with patch.object(unattended, "run_is_unattended", AsyncMock()) as asked:
            await manager._minus_ungated_writes([READ, NOT_COMPOSIO])
        asked.assert_not_awaited()


class TestTellingTheBotNobodyIsThere:
    """Bot 30 was built holding Gmail, its routine fired, and it answered:

        I can help set up this workflow. To start, should I fetch the last
        24h of Gmail now to show a sample summary?

    Which is a reasonable thing to say to a person, and there was no person.
    The runner hands the instruction over as a plain user message, so the
    bot answers it the way it answers anybody -- by offering, and waiting.
    At eight in the morning nothing answers, the turn ends, and the
    deliverable is a question.

    The runtime already knows: ``is_unattended`` is how the write gate
    decides. It simply never told the bot.
    """

    def test_the_instruction_survives(self):
        framed = unattended.briefing("Summarise my inbox", writes_allowed=False)
        assert "Summarise my inbox" in framed

    def test_it_says_nobody_can_answer(self):
        framed = unattended.briefing("Summarise my inbox", writes_allowed=False)
        lowered = framed.lower()
        assert "nobody" in lowered
        assert "question" in lowered

    def test_it_asks_for_the_work_not_an_offer(self):
        framed = unattended.briefing("Summarise my inbox", writes_allowed=False).lower()
        assert "now" in framed
        assert "report" in framed

    def test_it_says_what_to_do_when_it_cannot(self):
        """A run that produced nothing and a run that never happened look
        identical from outside. Saying what stopped it is the difference."""
        framed = unattended.briefing("Summarise my inbox", writes_allowed=False).lower()
        assert "stopped" in framed or "could not" in framed

    def test_a_gated_run_is_told_it_cannot_send(self):
        """Better than discovering it mid-turn: the model plans a report
        rather than planning a send and being refused."""
        framed = unattended.briefing("Chase them", writes_allowed=False).lower()
        assert "cannot send" in framed

    def test_an_allowed_run_is_not_told_that(self):
        framed = unattended.briefing("Chase them", writes_allowed=True).lower()
        assert "cannot send" not in framed

    def test_an_empty_instruction_still_frames(self):
        assert unattended.briefing("", writes_allowed=False).strip()
