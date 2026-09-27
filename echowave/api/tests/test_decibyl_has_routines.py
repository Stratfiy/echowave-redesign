"""Decibyl schedules its own work (KAN-156).

Routines belonged to bots: a row needed a workflow, the tick ran it on the
bot's text-chat engine. "Every weekday at 9, summarise the board" had
nowhere to go. Now a routine with no workflow is Decibyl's: proposed as a
card from the thread (schedule_routine -> propose_action), saved on
Confirm untested and unarmed like every routine, tested and armed from the
same screen, and run by Decibyl's own turn with the instruction as the one
user line -- reads run, writes stay cards, exactly as on the thread.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client as client
from api.services.workflow import actions, decibyl, routine_runner, routines

pytestmark = pytest.mark.asyncio


class TestTheTool:
    def test_it_is_offered_and_named(self):
        names = [t["name"] for t in decibyl.office_tools()]
        assert routines.SCHEDULE_TOOL_NAME in names
        assert routines.SCHEDULE_TOOL_NAME in "".join(decibyl.SYSTEM)
        schema = routines.schedule_tool_schema()
        assert set(schema["parameters"]["required"]) == {"name", "instruction", "when"}

    async def test_the_words_become_a_card_that_says_the_schedule_back(self):
        with patch(
            "api.services.workflow.actions.propose",
            new=AsyncMock(return_value={"status": "proposed", "event_id": 5}),
        ) as proposed:
            out = await routines.propose_schedule(
                organization_id=7,
                arguments={
                    "name": "Morning board",
                    "instruction": "Summarise what is blocked on the board.",
                    "when": "every weekday at 9am",
                },
            )
        assert out["status"] == "proposed"
        args = proposed.await_args.kwargs["arguments"]
        assert args["action"] == actions.SCHEDULE_ROUTINE
        assert args["cadence"] == "weekdays" and args["anchor"] == "clock"
        assert args["at_minute"] == 540
        assert "9:00" in args["said"] or "09:00" in args["said"]
        assert proposed.await_args.kwargs["workflow_id"] is None
        assert proposed.await_args.kwargs["in_channel"] is False

    async def test_words_it_cannot_read_are_refused_with_examples(self):
        out = await routines.propose_schedule(
            organization_id=7,
            arguments={
                "name": "x",
                "instruction": "y",
                "when": "whenever mercury is in retrograde",
            },
        )
        assert out["status"] == "not_proposed"
        assert "every" in out["reason"]

    async def test_decibyls_turn_reaches_it(self):
        with patch(
            "api.services.workflow.routines.propose_schedule",
            new=AsyncMock(return_value={"status": "proposed"}),
        ) as tool:
            await decibyl._tool(
                7,
                SimpleNamespace(
                    name=routines.SCHEDULE_TOOL_NAME,
                    arguments={
                        "name": "n",
                        "instruction": "i",
                        "when": "every day at 8am",
                    },
                ),
            )
        assert tool.await_args.kwargs["organization_id"] == 7


class TestTheCard:
    async def test_resolving_carries_the_schedule_and_a_label(self):
        payload = await actions.resolve(
            organization_id=7,
            workflow_id=None,
            arguments={
                "action": actions.SCHEDULE_ROUTINE,
                "name": "Morning board",
                "instruction": "Summarise the board.",
                "cadence": "weekdays",
                "anchor": "clock",
                "at_minute": 540,
                "offset_minutes": 0,
                "weekday": 0,
                "said": "every weekday at 09:00",
                "why": "asked on the thread",
            },
        )
        assert payload["label"] == "Schedule Morning board: every weekday at 09:00"
        assert payload["args"]["workflow_id"] is None
        assert payload["reversible"] is True

    async def test_confirming_saves_it_off_and_untested(self):
        created = SimpleNamespace(
            id=31, name="Morning board", is_active=False, tested_at=None
        )
        with patch.object(
            client, "create_routine", new=AsyncMock(return_value=created)
        ) as create:
            line = await actions._execute(
                organization_id=7,
                payload={
                    "action": actions.SCHEDULE_ROUTINE,
                    "args": {
                        "workflow_id": None,
                        "name": "Morning board",
                        "instruction": "Summarise the board.",
                        "cadence": "weekdays",
                        "anchor": "clock",
                        "at_minute": 540,
                        "offset_minutes": 0,
                        "weekday": 0,
                        "said": "every weekday at 09:00",
                    },
                    "confirmed": {"by": 32},
                },
            )
        kwargs = create.await_args.kwargs
        assert kwargs["organization_id"] == 7 and kwargs["workflow_id"] is None
        assert kwargs["cadence"] == "weekdays" and kwargs["at_minute"] == 540
        assert kwargs.get("is_active", False) is False
        assert "Test it" in line or "test" in line.lower()


class TestTheRun:
    def _routine(self):
        return {
            "id": 31,
            "organization_id": 7,
            "workflow_id": None,
            "name": "Morning board",
            "instruction": "Summarise what is blocked on the board.",
        }

    async def test_decibyl_answers_the_instruction_and_the_answer_is_a_deliverable(
        self,
    ):
        with (
            patch(
                "api.services.workflow.routine_runner._load",
                new=AsyncMock(return_value=self._routine()),
            ),
            patch(
                "api.services.workflow.decibyl.answer",
                new=AsyncMock(return_value="Two tasks are blocked: …"),
            ) as answer,
            patch(
                "api.services.workflow.agent_timeline.record", new=AsyncMock()
            ) as record,
            patch(
                "api.services.billing.events.charge_in_own_session",
                new=AsyncMock(return_value=1),
            ),
        ):
            result = await routine_runner.run_routine(31)
        assert result is None  # no workflow run: it is a turn on the thread
        assert answer.await_args.args[0] == 7
        assert "Summarise what is blocked" in answer.await_args.args[1]
        kinds = [c.kwargs["kind"] for c in record.await_args_list]
        assert "deliverable" in kinds
        deliverable = next(
            c.kwargs
            for c in record.await_args_list
            if c.kwargs["kind"] == "deliverable"
        )
        assert deliverable["workflow_id"] is None
        assert deliverable["payload"]["routine_id"] == 31

    async def test_a_failed_turn_is_a_could_not_not_silence(self):
        with (
            patch(
                "api.services.workflow.routine_runner._load",
                new=AsyncMock(return_value=self._routine()),
            ),
            patch(
                "api.services.workflow.decibyl.answer",
                new=AsyncMock(side_effect=RuntimeError("model down")),
            ),
            patch(
                "api.services.workflow.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            await routine_runner.run_routine(31)
        kinds = [c.kwargs["kind"] for c in record.await_args_list]
        assert "could_not" in kinds

    def test_a_scheduled_turn_is_unattended(self):
        # The briefing Decibyl gets says nobody is on the thread and writes wait.
        text = routine_runner.decibyl_briefing("Summarise the board.")
        assert "Summarise the board." in text
        assert "nobody" in text.lower() or "unattended" in text.lower()
