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
        finished = _task(status="blocked", from_workflow_id=None, result="No number")
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
                status="blocked",
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
                AsyncMock(return_value=_task(status="blocked")),
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


class TestATaskRefusedForCreditWaits:
    """Seen live: a request filed on an account with no credit landed in
    Done, badged COULD NOT, beside the finished ones. Nothing was tried, so
    nothing failed: it waits for a person to add credit and press Run again,
    which is what the Waiting column is for."""

    async def test_it_is_filed_as_waiting_with_the_reason(self):
        row = SimpleNamespace(
            id=12,
            organization_id=7,
            assignee_workflow_id=4,
            from_workflow_id=None,
            title="Say hello",
            brief="One line.",
            status="todo",
            result=None,
            due_at=None,
            created_by=None,
            created_at=None,
            started_at=None,
            finished_at=None,
            workflow_run_id=None,
            source_run_id=None,
            depth=0,
        )
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = AsyncMock(return_value=row)
        refused = SimpleNamespace(
            has_quota=False,
            error_message="Replies stop when your balance falls below ₹20.",
        )
        with (
            patch.object(tasks_board.db_client, "async_session", return_value=session),
            patch.object(
                tasks_board.db_client,
                "get_all_workflows_for_listing",
                AsyncMock(
                    return_value=[
                        SimpleNamespace(id=4, name="Front desk", handle="front")
                    ]
                ),
            ),
            patch.object(tasks_board.db_client, "update_task", AsyncMock()),
            patch.object(
                tasks_board.db_client,
                "create_workflow_run",
                AsyncMock(return_value=SimpleNamespace(id=99)),
            ),
            patch(
                "api.services.quota_service.authorize_workflow_run_start",
                AsyncMock(return_value=refused),
            ),
            patch("pipecat.utils.run_context.set_current_run_id"),
            patch.object(tasks_board, "_finish", AsyncMock()) as finish,
        ):
            await tasks_board.run_task(12)
        kwargs = finish.await_args.kwargs
        assert kwargs["status"] == tasks_board.WAITING
        assert kwargs["result"].startswith("Could not start: Replies stop")

    async def test_the_office_hears_it_could_not_start(self):
        waiting = _task(
            status="blocked", from_workflow_id=None, result="Could not start: x"
        )
        with (
            patch.object(
                tasks_board.db_client, "update_task", AsyncMock(return_value=waiting)
            ),
            patch.object(tasks_board.agent_timeline, "record", AsyncMock()) as record,
            patch.object(tasks_board.agent_timeline, "record_activity", AsyncMock()),
            patch("api.tasks.arq.enqueue_job", AsyncMock()),
        ):
            await tasks_board._finish(
                12,
                organization_id=7,
                status="blocked",
                result="Could not start: x",
                run_id=99,
                from_id=None,
                assignee_name="Retention",
                title="Say hello",
            )
        assert "could not start the task" in record.await_args.kwargs["summary"]


# --- TB-2: the checkout, the mention, the card's activity -----------------------


@pytest.mark.asyncio
class TestTheCheckout:
    async def test_a_task_someone_else_took_is_not_run_twice(self):
        row = _task(organization_id=7)

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, model, task_id):
                return row

        with (
            patch.object(tasks_board.db_client, "async_session", lambda: _Session()),
            patch.object(
                tasks_board.db_client,
                "get_all_workflows_for_listing",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                tasks_board.db_client, "update_task", AsyncMock(return_value=None)
            ) as update,
            patch.object(
                tasks_board.db_client, "create_workflow_run", AsyncMock()
            ) as create_run,
        ):
            assert await tasks_board.run_task(12) is None
        assert update.await_args.kwargs["unless_status"] == (
            tasks_board.IN_PROGRESS,
            *tasks_board.TERMINAL,
        )
        create_run.assert_not_awaited()

    async def test_the_checkout_is_a_locked_conditional_update(self, async_session):
        """Against the real table: a running task cannot be claimed again."""
        import contextlib

        from api.db import db_client
        from api.db.models import AgentTaskModel, OrganizationModel

        @contextlib.asynccontextmanager
        async def _same():
            yield async_session

        org = OrganizationModel(provider_id="tb2-checkout")
        async_session.add(org)
        await async_session.flush()
        task = AgentTaskModel(
            organization_id=org.id, title="Call back", brief="", status="todo"
        )
        async_session.add(task)
        await async_session.commit()

        claim = dict(
            organization_id=org.id,
            status="in_progress",
            unless_status=("in_progress", "done", "cancelled"),
        )
        with patch.object(db_client, "async_session", _same):
            first = await db_client.update_task(task.id, **claim)
            second = await db_client.update_task(task.id, **claim)
        assert first is not None and first.started_at is not None
        assert second is None


class TestTheRunReadsTheCard:
    def test_the_card_s_lines_come_after_the_brief(self):
        text = tasks_board.run_message(
            title="Chase invoice",
            brief="INV-9, Kriti Labs",
            asker="the team",
            thread=["- a person: @billing they paid half, chase the rest"],
        )
        assert text.index("INV-9") < text.index("they paid half")

    def test_a_card_with_no_lines_reads_as_it_always_did(self):
        assert tasks_board.run_message(
            title="t", brief="b", asker="a"
        ) == tasks_board.run_message(title="t", brief="b", asker="a", thread=[])


@pytest.mark.asyncio
class TestAMentionWakesABot:
    ROSTER = [
        SimpleNamespace(id=3, name="Billing", handle="billing"),
        SimpleNamespace(id=4, name="Sales", handle="sales"),
    ]

    async def _say(self, body, *, status="in_review"):
        task = _task(status=status, assignee_workflow_id=None)
        with (
            patch.object(
                tasks_board.db_client, "get_task", AsyncMock(return_value=task)
            ),
            patch.object(
                tasks_board.db_client,
                "add_task_comment",
                AsyncMock(
                    return_value=SimpleNamespace(
                        id=1,
                        task_id=task.id,
                        body=body,
                        author_user_id=42,
                        author_workflow_id=None,
                        created_at=None,
                    )
                ),
            ) as add,
            patch.object(
                tasks_board.db_client,
                "get_all_workflows_for_listing",
                AsyncMock(return_value=self.ROSTER),
            ),
            patch.object(tasks_board.db_client, "update_task", AsyncMock()) as update,
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue,
        ):
            out = await tasks_board.comment(
                organization_id=7, task_id=task.id, body=body, user_id=42
            )
        return out, update, enqueue, add

    async def test_naming_a_bot_hands_it_the_card_and_runs_it(self):
        out, update, enqueue, add = await self._say("@billing can you chase this?")
        assert out["woke"] == "@billing"
        fields = update.await_args.kwargs
        assert fields["assignee_workflow_id"] == 3
        assert fields["status"] == tasks_board.TODO
        enqueue.assert_awaited_once()
        assert "Handed to @billing" in add.await_args_list[-1].kwargs["body"]

    async def test_an_email_address_is_not_a_mention(self):
        out, update, enqueue, _ = await self._say("mail billing@kriti.in")
        assert out["woke"] is None
        update.assert_not_awaited()
        enqueue.assert_not_awaited()

    async def test_a_running_card_is_left_to_the_bot_on_it(self):
        out, update, enqueue, _ = await self._say("@sales fyi", status="in_progress")
        assert out["woke"] is None
        enqueue.assert_not_awaited()


# --- TB-3: labels -------------------------------------------------------------


class TestLabels:
    def test_they_are_trimmed_deduplicated_and_kept_as_first_written(self):
        assert tasks_board.clean_labels(
            ["  Billing ", "billing", "VIP  client", ""]
        ) == [
            "Billing",
            "VIP client",
        ]

    def test_a_comma_list_is_read_too(self):
        assert tasks_board.clean_labels("urgent, follow-up") == ["urgent", "follow-up"]

    def test_none_and_empty_mean_no_labels(self):
        assert tasks_board.clean_labels(None) is None
        assert tasks_board.clean_labels([" ", ""]) is None

    def test_too_long_or_too_many_is_refused_not_cut(self):
        with pytest.raises(tasks_board.TaskError, match="at most 24"):
            tasks_board.clean_labels(["x" * 25])
        with pytest.raises(tasks_board.TaskError, match="At most 8"):
            tasks_board.clean_labels([f"l{i}" for i in range(9)])

    def test_the_board_lists_each_label_once(self):
        tasks = [
            _task(labels=["Billing", "urgent"]),
            _task(labels=["billing"]),
            _task(labels=None),
        ]
        assert tasks_board.board_labels(tasks) == ["Billing", "urgent"]

    def test_a_card_carries_its_labels(self):
        assert tasks_board.as_dict(_task(labels=["urgent"]))["labels"] == ["urgent"]
        assert tasks_board.as_dict(_task())["labels"] == []

    @pytest.mark.asyncio
    async def test_editing_labels_writes_the_cleaned_list(self):
        with (
            patch.object(
                tasks_board.db_client, "get_task", AsyncMock(return_value=_task())
            ),
            patch.object(
                tasks_board.db_client,
                "update_task",
                AsyncMock(return_value=_task(labels=["urgent"])),
            ) as update,
        ):
            out = await tasks_board.edit(
                organization_id=7,
                task_id=12,
                changes={"labels": ["urgent", "Urgent"]},
                user_id=42,
            )
        assert update.await_args.kwargs["labels"] == ["urgent"]
        assert out["labels"] == ["urgent"]

    @pytest.mark.asyncio
    async def test_the_column_exists_and_round_trips(self, async_session):
        from api.db.models import AgentTaskModel, OrganizationModel

        org = OrganizationModel(provider_id="tb3-labels")
        async_session.add(org)
        await async_session.flush()
        task = AgentTaskModel(
            organization_id=org.id, title="t", brief="", labels=["urgent"]
        )
        async_session.add(task)
        await async_session.flush()
        await async_session.refresh(task)
        assert task.labels == ["urgent"]
