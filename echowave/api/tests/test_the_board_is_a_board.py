"""The task board on paperclip's issue model (TB-1).

Arrival tests. With the flag on: an agent's answer lands in review, not
done, and a person signs it off; the report is a line on the card under the
agent's name; a card carries a priority, a person as owner, a parent, what
it waits on, and an identifier counted per workspace; a person moves a card
to any of the seven columns, and a bot's task sent back from review runs
again. With the flag off: the five old columns under their new names, an
answer goes straight to done, and the new columns are refused.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import AgentEventKind
from api.services.workflow import tasks_board


def _bot(id: int, name: str, handle: str):
    return SimpleNamespace(id=id, name=name, handle=handle)


ROSTER = [_bot(3, "Front desk", "reception"), _bot(4, "Retention", "retention")]


def _task(**overrides):
    base = {
        "id": 12,
        "number": 42,
        "title": "Follow up Mrs Lakshmi",
        "brief": "Call in 3 days about the Tuesday slot.",
        "status": "todo",
        "priority": "medium",
        "from_workflow_id": 3,
        "assignee_workflow_id": 4,
        "assignee_user_id": None,
        "parent_id": None,
        "blocked_by": None,
        "created_by": None,
        "source_run_id": 70,
        "workflow_run_id": None,
        "depth": 0,
        "due_at": None,
        "result": None,
        "created_at": datetime(2026, 9, 22, tzinfo=UTC),
        "started_at": None,
        "finished_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _on(monkeypatch, value: bool = True):
    monkeypatch.setattr("api.constants.TASK_BOARD_2026_09_ENABLED", value)


class TestTheColumns:
    def test_paperclips_seven_and_the_old_five_under_new_names(self):
        assert tasks_board.STATUSES == (
            "backlog",
            "todo",
            "in_progress",
            "in_review",
            "done",
            "blocked",
            "cancelled",
        )
        assert tasks_board.LEGACY_STATUSES == (
            "todo",
            "in_progress",
            "blocked",
            "done",
            "cancelled",
        )
        # The old names still resolve, for the callers that say them.
        assert tasks_board.DOING == "in_progress"
        assert tasks_board.WAITING == tasks_board.COULD_NOT == "blocked"

    def test_an_answer_lands_in_review_on_the_board_and_in_done_off_it(
        self, monkeypatch
    ):
        _on(monkeypatch, True)
        assert tasks_board.landing_status("Booked.") == "in_review"
        assert tasks_board.landing_status("") == "blocked"
        _on(monkeypatch, False)
        assert tasks_board.landing_status("Booked.") == "done"
        assert tasks_board.landing_status("") == "blocked"


class TestTheIdentifier:
    def test_the_prefix_is_the_letters_a_person_would_use(self):
        assert tasks_board.identifier_prefix("Decibyl") == "DEC"
        assert tasks_board.identifier_prefix("Kovai Fertility Centre") == "KFC"
        assert tasks_board.identifier_prefix("Narayani Dental") == "ND"
        assert tasks_board.identifier_prefix(None) == "T"

    def test_the_card_says_it(self):
        out = tasks_board.as_dict(_task(), prefix="DEC")
        assert out["identifier"] == "DEC-42" and out["number"] == 42
        assert tasks_board.as_dict(_task(number=None))["identifier"] is None


@pytest.mark.asyncio
class TestFilingOnTheBoard:
    async def _create(self, arguments, monkeypatch, *, enabled=True):
        _on(monkeypatch, enabled)
        with (
            patch.object(
                tasks_board.db_client,
                "get_all_workflows_for_listing",
                AsyncMock(return_value=ROSTER),
            ),
            patch.object(
                tasks_board.db_client, "get_workflow_run", AsyncMock(return_value=None)
            ),
            patch.object(
                tasks_board.db_client, "create_task", AsyncMock(return_value=_task())
            ) as create,
            patch.object(tasks_board.agent_timeline, "record_activity", AsyncMock()),
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue,
        ):
            result = await tasks_board.create(
                organization_id=7,
                from_workflow_id=None,
                workflow_run_id=None,
                arguments=arguments,
                created_by=42,
            )
        return result, create, enqueue

    async def test_a_person_can_be_the_owner_and_no_job_starts(self, monkeypatch):
        result, create, enqueue = await self._create(
            {"title": "Approve the refund", "brief": "Rs 1,200", "assignee": "user:9"},
            monkeypatch,
        )
        assert result["status"] == "filed"
        kwargs = create.await_args.kwargs
        assert kwargs["assignee_user_id"] == 9
        assert kwargs["assignee_workflow_id"] is None
        enqueue.assert_not_awaited()

    async def test_priority_parent_and_blockers_ride_the_card(self, monkeypatch):
        _, create, _ = await self._create(
            {
                "title": "x",
                "brief": "y",
                "assignee": "team",
                "priority": "HIGH",
                "parent_id": "5",
                "blocked_by": [6, "7", "nope"],
                "backlog": True,
            },
            monkeypatch,
        )
        kwargs = create.await_args.kwargs
        assert kwargs["priority"] == "high"
        assert kwargs["parent_id"] == 5 and kwargs["blocked_by"] == [6, 7]
        assert kwargs["status"] == "backlog"

    async def test_a_priority_off_the_list_is_refused(self, monkeypatch):
        result, create, _ = await self._create(
            {"title": "x", "brief": "y", "assignee": "team", "priority": "urgent"},
            monkeypatch,
        )
        assert result["status"] == "not_filed" and "critical" in result["reason"]
        create.assert_not_awaited()

    async def test_backlog_is_todo_with_the_flag_off(self, monkeypatch):
        _, create, _ = await self._create(
            {"title": "x", "brief": "y", "assignee": "team", "backlog": True},
            monkeypatch,
            enabled=False,
        )
        assert create.await_args.kwargs["status"] == "todo"

    def test_the_tool_offers_the_priority(self):
        props = tasks_board.tool_properties()
        assert props["priority"]["enum"] == ["critical", "high", "medium", "low"]
        assert "priority" not in tasks_board.tool_schema()["parameters"]["required"]


@pytest.mark.asyncio
class TestHandingOverForReview:
    async def test_the_report_is_a_deliverable_a_comment_and_a_line_to_the_asker(
        self, monkeypatch
    ):
        _on(monkeypatch, True)
        finished = _task(
            status="in_review", result="Booked Tuesday.", workflow_run_id=99
        )
        with (
            patch.object(
                tasks_board.db_client, "update_task", AsyncMock(return_value=finished)
            ),
            patch.object(
                tasks_board.db_client, "add_task_comment", AsyncMock()
            ) as note,
            patch.object(tasks_board.agent_timeline, "record", AsyncMock()) as record,
            patch.object(
                tasks_board.agent_timeline, "record_activity", AsyncMock()
            ) as activity,
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue,
        ):
            await tasks_board._finish(
                12,
                organization_id=7,
                status="in_review",
                result="Booked Tuesday.",
                run_id=99,
                from_id=3,
                assignee_name="Retention",
                title="Follow up Mrs Lakshmi",
            )
        assert record.await_args.kwargs["kind"] == AgentEventKind.DELIVERABLE.value
        assert "handed over for review" in activity.await_args.kwargs["summary"]
        assert note.await_args.kwargs["author_workflow_id"] == 4
        assert note.await_args.kwargs["body"] == "Booked Tuesday."
        assert enqueue.await_args.args[1] == 3

    async def test_off_the_board_no_comment_is_written(self, monkeypatch):
        _on(monkeypatch, False)
        with (
            patch.object(
                tasks_board.db_client,
                "update_task",
                AsyncMock(return_value=_task(status="done", result="x")),
            ),
            patch.object(
                tasks_board.db_client, "add_task_comment", AsyncMock()
            ) as note,
            patch.object(tasks_board.agent_timeline, "record", AsyncMock()),
            patch.object(tasks_board.agent_timeline, "record_activity", AsyncMock()),
            patch("api.tasks.arq.enqueue_job", AsyncMock()),
        ):
            await tasks_board._finish(
                12,
                organization_id=7,
                status="done",
                result="x",
                run_id=99,
                from_id=None,
                assignee_name="Retention",
                title="t",
            )
        note.assert_not_awaited()


@pytest.mark.asyncio
class TestThePersonsHalfOnTheBoard:
    async def _move(self, before: str, to: str, monkeypatch, *, enabled=True):
        _on(monkeypatch, enabled)
        with (
            patch.object(
                tasks_board.db_client,
                "get_task",
                AsyncMock(return_value=_task(status=before)),
            ),
            patch.object(
                tasks_board.db_client,
                "update_task",
                AsyncMock(return_value=_task(status=to)),
            ),
            patch.object(
                tasks_board.agent_timeline, "record_activity", AsyncMock()
            ) as activity,
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue,
        ):
            out = await tasks_board.set_status(
                organization_id=7, task_id=12, status=to, result=None, user_id=42
            )
        return out, activity, enqueue

    async def test_done_from_review_is_a_sign_off(self, monkeypatch):
        out, activity, _ = await self._move("in_review", "done", monkeypatch)
        assert out["status"] == "done"
        assert "signed off" in activity.await_args.kwargs["summary"]

    async def test_sent_back_from_review_a_bots_task_runs_again(self, monkeypatch):
        _, _, enqueue = await self._move("in_review", "todo", monkeypatch)
        assert enqueue.await_args.args[1] == 12

    async def test_cancelled_is_a_row(self, monkeypatch):
        _, activity, _ = await self._move("todo", "cancelled", monkeypatch)
        assert "cancelled" in activity.await_args.kwargs["summary"]

    async def test_backlog_and_review_are_refused_with_the_flag_off(self, monkeypatch):
        for column in ("backlog", "in_review"):
            with pytest.raises(tasks_board.TaskError):
                await self._move("todo", column, monkeypatch, enabled=False)
        out, _, _ = await self._move("todo", "in_progress", monkeypatch, enabled=False)
        assert out["status"] == "in_progress"


@pytest.mark.asyncio
class TestTheCardsFields:
    async def _edit(self, changes, *, task=None):
        with (
            patch.object(
                tasks_board.db_client,
                "get_task",
                AsyncMock(return_value=task or _task()),
            ),
            patch.object(
                tasks_board.db_client,
                "get_all_workflows_for_listing",
                AsyncMock(return_value=ROSTER),
            ),
            patch.object(
                tasks_board.db_client, "update_task", AsyncMock(return_value=_task())
            ) as update,
        ):
            await tasks_board.edit(
                organization_id=7, task_id=12, changes=changes, user_id=42
            )
        return update

    async def test_a_person_replaces_the_bot_and_a_bot_replaces_the_person(self):
        update = await self._edit({"assignee": "user:9"})
        fields = update.await_args.kwargs
        assert (
            fields["assignee_user_id"] == 9 and fields["assignee_workflow_id"] is None
        )
        update = await self._edit({"assignee": "@reception"})
        fields = update.await_args.kwargs
        assert (
            fields["assignee_workflow_id"] == 3 and fields["assignee_user_id"] is None
        )

    async def test_a_card_is_not_its_own_parent_or_blocker(self):
        with pytest.raises(tasks_board.TaskError):
            await self._edit({"parent_id": 12})
        update = await self._edit({"blocked_by": [12, 13]})
        assert update.await_args.kwargs["blocked_by"] == [13]

    async def test_priority_and_due(self):
        update = await self._edit({"priority": "critical", "due": "in 2 hours"})
        fields = update.await_args.kwargs
        assert fields["priority"] == "critical" and fields["due_at"] is not None

    async def test_an_unknown_bot_is_refused(self):
        with pytest.raises(tasks_board.TaskError):
            await self._edit({"assignee": "@nobody"})


@pytest.mark.asyncio
class TestComments:
    async def test_a_persons_line_lands_on_the_card(self):
        row = SimpleNamespace(
            id=1,
            task_id=12,
            body="Looks right, send it.",
            author_user_id=42,
            author_workflow_id=None,
            created_at=datetime(2026, 9, 22, tzinfo=UTC),
        )
        with (
            patch.object(
                tasks_board.db_client, "get_task", AsyncMock(return_value=_task())
            ),
            patch.object(
                tasks_board.db_client, "add_task_comment", AsyncMock(return_value=row)
            ) as add,
        ):
            out = await tasks_board.comment(
                organization_id=7,
                task_id=12,
                body="  Looks right, send it. ",
                user_id=42,
            )
        assert add.await_args.kwargs["body"] == "Looks right, send it."
        assert out["author_user_id"] == 42 and out["task_id"] == 12

    async def test_an_empty_line_is_refused(self):
        with pytest.raises(tasks_board.TaskError):
            await tasks_board.comment(
                organization_id=7, task_id=12, body="   ", user_id=42
            )

    def test_the_author_is_named_from_either_side(self):
        row = SimpleNamespace(
            id=1,
            task_id=12,
            body="x",
            author_user_id=None,
            author_workflow_id=4,
            created_at=None,
        )
        out = tasks_board.comment_dict(row, {4: "Retention"}, {42: "Nithish"})
        assert out["author_name"] == "Retention"
        row.author_workflow_id, row.author_user_id = None, 42
        assert (
            tasks_board.comment_dict(row, {}, {42: "Nithish"})["author_name"]
            == "Nithish"
        )


class TestTheHomeCount:
    def test_stuck_means_blocked_or_waiting_for_review(self):
        from api.services.workflow import home_openers

        rows = [
            _task(status="blocked"),
            _task(status="in_review"),
            _task(status="done"),
            _task(status="todo"),
        ]
        import asyncio

        with patch.object(
            home_openers.db_client,
            "tasks_for_organization",
            AsyncMock(return_value=rows),
        ):
            assert asyncio.run(home_openers.stuck_tasks(7)) == 2
