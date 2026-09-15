"""The task board (KAN-140 P1): the one surface people and bots work from.

Arrival tests: a bot's task reaches the board, the office thread, the
asker's thread and a job; a task to itself, to nobody, or too many
hand-offs deep is refused with a reason; a finished task tells the asker
in its own chat; a person's status change is a row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import AgentEventKind
from api.services.billing import events as billing_events
from api.services.workflow import decibyl, tasks_board
from api.tasks.function_names import FunctionNames


def _bot(id: int, name: str, handle: str):
    return SimpleNamespace(id=id, name=name, handle=handle)


ROSTER = [_bot(3, "Front desk", "reception"), _bot(4, "Retention", "retention")]


def _task(**overrides):
    base = {
        "id": 12,
        "title": "Follow up Mrs Lakshmi",
        "brief": "Call in 3 days about the Tuesday slot.",
        "status": "todo",
        "from_workflow_id": 3,
        "assignee_workflow_id": 4,
        "created_by": None,
        "source_run_id": 70,
        "workflow_run_id": None,
        "depth": 0,
        "due_at": None,
        "result": None,
        "created_at": datetime(2026, 9, 15, tzinfo=UTC),
        "started_at": None,
        "finished_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestDue:
    def test_in_n_days_and_iso_and_nothing(self):
        now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
        assert tasks_board.parse_due("in 3 days", now=now) == now + timedelta(days=3)
        assert tasks_board.parse_due("in 2 hours", now=now) == now + timedelta(hours=2)
        assert (
            tasks_board.parse_due("2026-09-20", now=now).date().isoformat()
            == "2026-09-20"
        )
        assert tasks_board.parse_due("whenever", now=now) is None
        assert tasks_board.parse_due("", now=now) is None


@pytest.mark.asyncio
class TestFiling:
    async def _create(self, arguments, *, from_id=3, run_id=70, run=None):
        with (
            patch.object(
                tasks_board.db_client,
                "get_all_workflows_for_listing",
                AsyncMock(return_value=ROSTER),
            ),
            patch.object(
                tasks_board.db_client, "get_workflow_run", AsyncMock(return_value=run)
            ),
            patch.object(
                tasks_board.db_client, "create_task", AsyncMock(return_value=_task())
            ) as create,
            patch.object(tasks_board.db_client, "update_task", AsyncMock()),
            patch.object(
                tasks_board.agent_timeline, "record_activity", AsyncMock()
            ) as activity,
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue,
        ):
            result = await tasks_board.create(
                organization_id=7,
                from_workflow_id=from_id,
                workflow_run_id=run_id,
                arguments=arguments,
            )
        return result, create, activity, enqueue

    async def test_a_bots_task_for_a_colleague_is_a_row_two_lines_and_a_job(self):
        result, create, activity, enqueue = await self._create(
            {
                "title": "Follow up Mrs Lakshmi",
                "brief": "Call in 3 days about the Tuesday slot.",
                "assignee": "@retention",
                "due": "in 3 days",
            }
        )
        assert result["status"] == "filed" and result["task_id"] == 12
        kwargs = create.await_args.kwargs
        assert kwargs["assignee_workflow_id"] == 4 and kwargs["from_workflow_id"] == 3
        assert kwargs["due_at"] is not None and kwargs["depth"] == 0
        # Decibyl's thread, then the asker's own chat.
        assert activity.await_count == 2
        office_line, asker_line = [c.kwargs for c in activity.await_args_list]
        assert (
            office_line["in_channel"] is False
            and "Front desk filed a task" in office_line["summary"]
        )
        assert asker_line["workflow_id"] == 3
        assert enqueue.await_args.args == (FunctionNames.RUN_AGENT_TASK, 12)

    async def test_a_task_for_the_team_starts_no_job(self):
        result, create, _, enqueue = await self._create(
            {
                "title": "Approve the refund",
                "brief": "Rs 1,200 to Meera",
                "assignee": "team",
            },
            from_id=None,
            run_id=None,
        )
        assert result["status"] == "filed"
        assert create.await_args.kwargs["assignee_workflow_id"] is None
        enqueue.assert_not_awaited()

    async def test_a_bot_cannot_file_to_itself(self):
        result, create, *_ = await self._create(
            {"title": "x", "brief": "y", "assignee": "@reception"}
        )
        assert result["status"] == "not_filed" and "yourself" in result["reason"]
        create.assert_not_awaited()

    async def test_an_unknown_assignee_is_said_not_guessed(self):
        result, create, *_ = await self._create(
            {"title": "x", "brief": "y", "assignee": "@sales"}
        )
        assert result["status"] == "not_filed" and "@handle" in result["reason"]
        create.assert_not_awaited()

    async def test_too_many_hand_offs_deep_is_refused(self):
        run = SimpleNamespace(
            annotations={"task": {"id": 1, "depth": tasks_board.MAX_DEPTH}}
        )
        result, create, *_ = await self._create(
            {"title": "x", "brief": "y", "assignee": "@retention"}, run=run
        )
        assert (
            result["status"] == "not_filed"
            and "ask a person" in result["reason"].lower()
        )
        create.assert_not_awaited()

    async def test_the_asker_run_is_one_deeper(self):
        run = SimpleNamespace(annotations={"task": {"id": 1, "depth": 0}})
        _, create, *_ = await self._create(
            {"title": "x", "brief": "y", "assignee": "@retention"}, run=run
        )
        assert create.await_args.kwargs["depth"] == 1


@pytest.mark.asyncio
class TestFinishing:
    async def test_a_done_task_is_a_deliverable_and_the_asker_is_told_in_its_chat(self):
        finished = _task(
            status="done", result="Booked Tuesday 5 pm.", workflow_run_id=99
        )
        with (
            patch.object(
                tasks_board.db_client, "update_task", AsyncMock(return_value=finished)
            ),
            patch.object(tasks_board.agent_timeline, "record", AsyncMock()) as record,
            patch.object(tasks_board.agent_timeline, "record_activity", AsyncMock()),
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue,
        ):
            await tasks_board._finish(
                12,
                organization_id=7,
                status="done",
                result="Booked Tuesday 5 pm.",
                run_id=99,
                from_id=3,
                assignee_name="Retention",
                title="Follow up Mrs Lakshmi",
            )
        row = record.await_args.kwargs
        assert row["kind"] == AgentEventKind.DELIVERABLE.value
        assert row["workflow_id"] == 4 and row["workflow_run_id"] == 99
        assert enqueue.await_args.args[:2] == (FunctionNames.ANSWER_CHANNEL_MESSAGE, 3)
        assert "Booked Tuesday 5 pm." in enqueue.await_args.args[3]

    async def test_a_task_from_a_person_tells_nobody_back(self):
        finished = _task(status="could_not", from_workflow_id=None, result="No number")
        with (
            patch.object(
                tasks_board.db_client, "update_task", AsyncMock(return_value=finished)
            ),
            patch.object(tasks_board.agent_timeline, "record", AsyncMock()) as record,
            patch.object(tasks_board.agent_timeline, "record_activity", AsyncMock()),
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue,
        ):
            await tasks_board._finish(
                12,
                organization_id=7,
                status="could_not",
                result="No number",
                run_id=99,
                from_id=None,
                assignee_name="Retention",
                title="x",
            )
        assert record.await_args.kwargs["kind"] == AgentEventKind.COULD_NOT.value
        enqueue.assert_not_awaited()

    def test_the_brief_reaches_the_assignee_with_the_rules(self):
        text = tasks_board.run_message(
            title="Follow up", brief="Call Mrs L.", asker="@reception"
        )
        assert text.startswith("A task from @reception: Follow up")
        assert "Call Mrs L." in text and "Do not file this task back" in text


@pytest.mark.asyncio
class TestThePersonsHalf:
    async def test_marking_done_is_a_row(self):
        with (
            patch.object(
                tasks_board.db_client, "get_task", AsyncMock(return_value=_task())
            ),
            patch.object(
                tasks_board.db_client,
                "update_task",
                AsyncMock(return_value=_task(status="done")),
            ),
            patch.object(
                tasks_board.agent_timeline, "record_activity", AsyncMock()
            ) as activity,
        ):
            out = await tasks_board.set_status(
                organization_id=7,
                task_id=12,
                status="done",
                result="Did it",
                user_id=42,
            )
        assert out["status"] == "done"
        assert "marked the task done" in activity.await_args.kwargs["summary"]

    async def test_requeueing_a_finished_bot_task_runs_it_again(self):
        with (
            patch.object(
                tasks_board.db_client,
                "get_task",
                AsyncMock(return_value=_task(status="could_not")),
            ),
            patch.object(
                tasks_board.db_client,
                "update_task",
                AsyncMock(return_value=_task(status="todo")),
            ),
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue,
        ):
            await tasks_board.set_status(
                organization_id=7, task_id=12, status="todo", result=None, user_id=42
            )
        assert enqueue.await_args.args == (FunctionNames.RUN_AGENT_TASK, 12)

    async def test_a_column_that_does_not_exist_is_refused(self):
        with pytest.raises(tasks_board.TaskError):
            await tasks_board.set_status(
                organization_id=7,
                task_id=12,
                status="archived",
                result=None,
                user_id=42,
            )


class TestWiring:
    def test_decibyl_and_the_bots_carry_the_tool_and_it_is_priced(self):
        assert tasks_board.TOOL_NAME in [t["name"] for t in decibyl.TOOLS()]
        assert billing_events.credits_for(billing_events.TASK_RUN) == 1
