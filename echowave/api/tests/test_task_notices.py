"""A person is told when a task lands on them (KAN-157)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client as client
from api.services.workflow import task_notices

pytestmark = pytest.mark.asyncio


def _task(**overrides):
    base = {
        "id": 12,
        "number": 12,
        "title": "Chase the AWS invoice",
        "brief": "Ask billing for the July bill.",
        "assignee_user_id": 32,
        "created_by": 40,
        "status": "todo",
        "priority": "medium",
        "parent_id": None,
        "blocked_by": [],
        "labels": [],
        "from_workflow_id": None,
        "assignee_workflow_id": None,
        "workflow_run_id": None,
        "source_run_id": None,
        "depth": 0,
        "due_at": None,
        "result": None,
        "created_at": None,
        "started_at": None,
        "finished_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _patched(is_new=True, user_email="nithish@example.com"):
    return (
        patch(
            "api.services.notifications.inbox.post", new=AsyncMock(return_value=is_new)
        ),
        patch(
            "api.services.messaging.email.send_email",
            new=AsyncMock(return_value=SimpleNamespace(sent=True)),
        ),
        patch.object(
            client,
            "get_user_by_id",
            new=AsyncMock(return_value=SimpleNamespace(id=32, email=user_email)),
        ),
    )


class TestAssigned:
    async def test_the_bell_rings_and_one_mail_goes_with_the_link(self):
        p = _patched()
        with p[0] as post, p[1] as mail, p[2]:
            assert await task_notices.assigned(_task(), organization_id=7) is True
        assert post.await_args.kwargs["kind"] == task_notices.ASSIGNED
        assert post.await_args.kwargs["dedupe_key"] == "task:12:assigned:32"
        assert post.await_args.kwargs["link"] == "/tasks/12"
        assert mail.await_args.kwargs["to"] == "nithish@example.com"
        assert "Chase the AWS invoice" in mail.await_args.kwargs["subject"]
        assert "/tasks/12" in mail.await_args.kwargs["body_text"]

    async def test_a_re_save_posts_nothing_and_mails_nothing(self):
        p = _patched(is_new=False)
        with p[0], p[1] as mail, p[2]:
            assert await task_notices.assigned(_task(), organization_id=7) is False
        mail.assert_not_awaited()

    async def test_assigning_to_yourself_is_quiet(self):
        p = _patched()
        with p[0] as post, p[1], p[2]:
            assert (
                await task_notices.assigned(_task(), organization_id=7, by_user_id=32)
                is False
            )
        post.assert_not_awaited()

    async def test_a_task_for_the_team_or_an_agent_tells_no_person(self):
        p = _patched()
        with p[0] as post, p[1], p[2]:
            assert (
                await task_notices.assigned(
                    _task(assignee_user_id=None), organization_id=7
                )
                is False
            )
        post.assert_not_awaited()

    async def test_a_member_with_no_email_still_gets_the_bell(self):
        p = _patched(user_email=None)
        with p[0] as post, p[1] as mail, p[2]:
            assert await task_notices.assigned(_task(), organization_id=7) is True
        post.assert_awaited_once()
        mail.assert_not_awaited()


class TestReported:
    async def test_an_agents_report_reaches_the_holder_once_per_report(self):
        p = _patched()
        with p[0] as post, p[1] as mail, p[2]:
            await task_notices.reported(
                _task(),
                organization_id=7,
                body="Called, no answer.",
                agent_name="@retention",
            )
        assert post.await_args.kwargs["kind"] == task_notices.REPORTED
        assert post.await_args.kwargs["dedupe_key"].startswith("task:12:reported:")
        assert post.await_args.kwargs["title"] == "@retention on Chase the AWS invoice"
        assert mail.await_args.kwargs["body_text"].startswith("Called, no answer.")

    async def test_the_filer_is_told_when_nobody_holds_it(self):
        p = _patched()
        with p[0] as post, p[1], p[2]:
            await task_notices.reported(
                _task(assignee_user_id=None),
                organization_id=7,
                body="Done.",
                agent_name=None,
            )
        assert (
            post.await_args.kwargs["dedupe_key"] == post.await_args.kwargs["dedupe_key"]
        )
        assert "An agent on" in post.await_args.kwargs["title"]

    async def test_no_person_anywhere_means_no_notice(self):
        p = _patched()
        with p[0] as post, p[1], p[2]:
            assert (
                await task_notices.reported(
                    _task(assignee_user_id=None, created_by=None),
                    organization_id=7,
                    body="x",
                    agent_name=None,
                )
                is False
            )
        post.assert_not_awaited()


class TestBlocked:
    async def test_the_holder_is_told_unless_they_blocked_it_themselves(self):
        p = _patched()
        with p[0] as post, p[1], p[2]:
            assert (
                await task_notices.blocked(
                    _task(),
                    organization_id=7,
                    by_user_id=40,
                    reason="Need the PO number",
                )
                is True
            )
            assert (
                await task_notices.blocked(_task(), organization_id=7, by_user_id=32)
                is False
            )
        assert post.await_count == 1
        assert post.await_args.kwargs["title"] == "Blocked: Chase the AWS invoice"
        assert post.await_args.kwargs["body"] == "Need the PO number"


class TestNothingUnwinds:
    async def test_a_dead_mail_server_is_a_warning_not_an_error(self):
        with (
            patch(
                "api.services.notifications.inbox.post",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "api.services.messaging.email.send_email",
                new=AsyncMock(side_effect=RuntimeError("smtp down")),
            ),
            patch.object(
                client,
                "get_user_by_id",
                new=AsyncMock(return_value=SimpleNamespace(id=32, email="a@b.c")),
            ),
        ):
            assert await task_notices.assigned(_task(), organization_id=7) is False


class TestTheBoardSendsThem:
    """The four places a notice comes from, each reaching the module once."""

    async def test_filing_for_a_person_tells_them(self):
        from api.services.workflow import tasks_board

        created = _task(id=90, assignee_user_id=32, created_by=40)
        with (
            patch.object(client, "create_task", new=AsyncMock(return_value=created)),
            patch.object(
                client, "get_all_workflows_for_listing", new=AsyncMock(return_value=[])
            ),
            patch(
                "api.services.workflow.agent_timeline.record_activity", new=AsyncMock()
            ),
            patch(
                "api.services.workflow.task_notices.assigned",
                new=AsyncMock(return_value=True),
            ) as told,
        ):
            out = await tasks_board.create(
                organization_id=7,
                from_workflow_id=None,
                workflow_run_id=None,
                arguments={
                    "title": "Chase it",
                    "brief": "please",
                    "assignee": "user:32",
                },
                created_by=40,
            )
        assert out["status"] == "filed"
        assert told.await_args.kwargs["by_user_id"] == 40
        assert told.await_args.args[0].id == 90

    async def test_handing_a_card_to_a_person_tells_them(self):
        from api.services.workflow import tasks_board

        before = _task(assignee_user_id=None)
        after = _task(assignee_user_id=32)
        with (
            patch.object(client, "get_task", new=AsyncMock(return_value=before)),
            patch.object(client, "update_task", new=AsyncMock(return_value=after)),
            patch.object(
                client, "get_all_workflows_for_listing", new=AsyncMock(return_value=[])
            ),
            patch(
                "api.services.workflow.task_notices.assigned",
                new=AsyncMock(return_value=True),
            ) as told,
        ):
            await tasks_board.edit(
                organization_id=7,
                task_id=12,
                changes={"assignee": "user:32"},
                user_id=40,
            )
        assert told.await_args.kwargs["by_user_id"] == 40

    async def test_blocking_a_card_tells_its_holder(self):
        from api.services.workflow import tasks_board

        before = _task(status="in_progress")
        after = _task(status="blocked")
        with (
            patch.object(client, "get_task", new=AsyncMock(return_value=before)),
            patch.object(client, "update_task", new=AsyncMock(return_value=after)),
            patch.object(client, "add_task_comment", new=AsyncMock()),
            patch(
                "api.services.workflow.agent_timeline.record_activity", new=AsyncMock()
            ),
            patch(
                "api.services.workflow.task_notices.blocked",
                new=AsyncMock(return_value=True),
            ) as told,
        ):
            await tasks_board.set_status(
                organization_id=7,
                task_id=12,
                status="blocked",
                result="Need the PO",
                user_id=40,
            )
        assert told.await_args.kwargs["reason"] == "Need the PO"
        assert told.await_args.kwargs["by_user_id"] == 40

    async def test_an_agents_report_tells_the_holder(self):
        from api.services.workflow import tasks_board

        task = _task(
            assignee_workflow_id=4,
            from_workflow_id=None,
            workflow_run_id=None,
            source_run_id=None,
            result=None,
        )
        with (
            patch.object(client, "update_task", new=AsyncMock(return_value=task)),
            patch.object(client, "add_task_comment", new=AsyncMock()),
            patch.object(
                client, "get_workflow_by_id", new=AsyncMock(return_value=None)
            ),
            patch(
                "api.services.workflow.agent_timeline.record_activity", new=AsyncMock()
            ),
            patch("api.services.workflow.agent_timeline.record", new=AsyncMock()),
            patch(
                "api.services.workflow.task_notices.reported",
                new=AsyncMock(return_value=True),
            ) as told,
            patch(
                "api.services.workflow.task_notices.blocked",
                new=AsyncMock(return_value=True),
            ),
        ):
            await tasks_board._finish(
                12,
                organization_id=7,
                status="done",
                result="Called; they will pay Friday.",
                run_id=None,
                from_id=None,
                assignee_name="@retention",
                title="Chase the AWS invoice",
            )
        assert told.await_args.kwargs["agent_name"] == "@retention"
        assert "pay Friday" in told.await_args.kwargs["body"]
