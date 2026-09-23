"""A routine, a trigger, a task and a channel reply each run the bot for
one turn. The engine marks a run complete only when the conversation
reached an end node, which one turn never does, so all of them stayed
"In Progress" on the Logs screen for ever: 97 of the 214 runs on the live
account. Each runner now closes its run in a ``finally``, whatever the
turn did.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import WorkflowRunState
from api.services.workflow import (
    channel_reply,
    one_shot_run,
    routine_runner,
    tasks_board,
    trigger_runner,
)


@pytest.mark.asyncio
class TestClosing:
    async def test_a_run_is_marked_complete(self):
        with patch.object(
            one_shot_run.db_client, "update_workflow_run", AsyncMock()
        ) as update:
            await one_shot_run.close(41)
        update.assert_awaited_once_with(
            41, is_completed=True, state=WorkflowRunState.COMPLETED.value
        )

    async def test_no_run_means_nothing_to_close(self):
        with patch.object(
            one_shot_run.db_client, "update_workflow_run", AsyncMock()
        ) as update:
            await one_shot_run.close(None)
        update.assert_not_awaited()

    async def test_a_failed_mark_does_not_fail_the_run(self):
        with patch.object(
            one_shot_run.db_client,
            "update_workflow_run",
            AsyncMock(side_effect=RuntimeError("db away")),
        ):
            await one_shot_run.close(41)


def _run(run_id=41):
    return SimpleNamespace(id=run_id)


@pytest.mark.asyncio
class TestEveryOneShotRunnerCloses:
    """Each runner is driven to its first failure after the run row exists --
    no credit -- which is the earliest exit and the one most likely to be
    forgotten. The close must happen on that path, so it happens on all."""

    NO_QUOTA = SimpleNamespace(has_quota=False, error_message="no credit")

    async def test_a_routine(self):
        with (
            patch.object(
                routine_runner,
                "_load",
                AsyncMock(
                    return_value={
                        "id": 1,
                        "organization_id": 7,
                        "workflow_id": 3,
                        "name": "Morning brief",
                        "instruction": "Summarise the inbox",
                    }
                ),
            ),
            patch.object(
                routine_runner.db_client,
                "create_workflow_run",
                AsyncMock(return_value=_run()),
            ),
            patch.object(
                routine_runner,
                "authorize_workflow_run_start",
                AsyncMock(return_value=self.NO_QUOTA),
            ),
            patch.object(routine_runner.agent_timeline, "record", AsyncMock()),
            patch.object(one_shot_run, "close", AsyncMock()) as close,
        ):
            await routine_runner.run_routine(1)
        close.assert_awaited_once_with(41)

    async def test_a_trigger(self):
        with (
            patch.object(
                trigger_runner,
                "_load",
                AsyncMock(
                    return_value={
                        "id": 1,
                        "organization_id": 7,
                        "workflow_id": 3,
                        "name": "New order",
                        "instruction": "Check stock",
                        "fields": [],
                    }
                ),
            ),
            patch.object(
                trigger_runner.db_client,
                "create_workflow_run",
                AsyncMock(return_value=_run()),
            ),
            patch.object(
                trigger_runner,
                "authorize_workflow_run_start",
                AsyncMock(return_value=self.NO_QUOTA),
            ),
            patch.object(trigger_runner.agent_timeline, "record", AsyncMock()),
            patch.object(one_shot_run, "close", AsyncMock()) as close,
        ):
            await trigger_runner.run_trigger(1, {"order": 5})
        close.assert_awaited_once_with(41)

    async def test_a_task(self):
        row = SimpleNamespace(
            id=12,
            organization_id=7,
            title="Follow up",
            brief="Call back",
            assignee_workflow_id=4,
            from_workflow_id=3,
            depth=0,
            status="todo",
            result=None,
            workflow_run_id=None,
            due_at=None,
            created_by=None,
            created_at=None,
            started_at=None,
            finished_at=None,
        )

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, model, task_id):
                return row

        roster = [
            SimpleNamespace(id=3, name="Sales", handle="sales"),
            SimpleNamespace(id=4, name="Retention", handle="retention"),
        ]
        with (
            patch.object(tasks_board.db_client, "async_session", lambda: _Session()),
            patch.object(
                tasks_board.db_client,
                "get_all_workflows_for_listing",
                AsyncMock(return_value=roster),
            ),
            patch.object(
                tasks_board.db_client, "update_task", AsyncMock(return_value=row)
            ),
            patch.object(
                tasks_board.db_client,
                "create_workflow_run",
                AsyncMock(return_value=_run()),
            ),
            patch(
                "api.services.quota_service.authorize_workflow_run_start",
                AsyncMock(return_value=self.NO_QUOTA),
            ),
            patch.object(tasks_board.agent_timeline, "record", AsyncMock()),
            patch.object(tasks_board.agent_timeline, "record_activity", AsyncMock()),
            patch("api.tasks.arq.enqueue_job", AsyncMock()),
            patch.object(one_shot_run, "close", AsyncMock()) as close,
        ):
            await tasks_board.run_task(12)
        close.assert_awaited_once_with(41)

    async def test_a_channel_reply(self):
        with (
            patch.object(
                channel_reply.db_client,
                "get_workflow_by_id",
                AsyncMock(
                    return_value=SimpleNamespace(id=3, organization_id=7, name="Sales")
                ),
            ),
            patch.object(
                channel_reply.db_client,
                "create_workflow_run",
                AsyncMock(return_value=_run()),
            ),
            patch.object(
                channel_reply,
                "authorize_workflow_run_start",
                AsyncMock(return_value=self.NO_QUOTA),
            ),
            patch.object(channel_reply.agent_timeline, "record", AsyncMock()),
            patch.object(one_shot_run, "close", AsyncMock()) as close,
        ):
            await channel_reply.answer_in_channel(3, 9, "hello")
        close.assert_awaited_once_with(41)
