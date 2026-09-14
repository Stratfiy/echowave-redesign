"""A finished call is a line on the bot's thread, and a person can talk to a
bot on its own chat.

The first is the arrival test for the thing a phone bot spends its day doing:
every call left runs, recordings and costs, and a bot with a hundred calls
opened on "Nothing yet". The second is the composer that was missing from a
tab called Chat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.enums import AgentEventKind
from api.services.workflow import agent_timeline


def _run(**over):
    answered = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)
    base = dict(
        id=77,
        workflow_id=3,
        mode="plivo",
        call_type="inbound",
        answered_at=answered,
        ended_at=answered + timedelta(seconds=192),
        billable_seconds=192,
        gathered_context={"mapped_call_disposition": "booked"},
        recording_url="s3://x",
    )
    base.update(over)
    return SimpleNamespace(**base)


class TestTheLine:
    def test_length_and_how_it_ended(self):
        assert (
            agent_timeline.call_summary(
                answered=True, duration_seconds=192, disposition="booked"
            )
            == "Call · 3m12s · booked"
        )

    def test_an_unanswered_call_says_so(self):
        assert (
            agent_timeline.call_summary(
                answered=False, duration_seconds=0, disposition="no_answer"
            )
            == "Call not answered · no answer"
        )


@pytest.mark.asyncio
class TestTheRowIsWritten:
    async def test_a_voice_run_gets_its_row(self):
        with (
            patch(
                "api.services.workflow.agent_timeline.db_client.get_workflow_run",
                new=AsyncMock(return_value=_run()),
            ),
            patch(
                "api.services.workflow.agent_timeline.db_client.get_organization_id_by_workflow_run_id",
                new=AsyncMock(return_value=7),
            ),
            patch(
                "api.services.workflow.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            await agent_timeline.record_call_ended(77)
        kwargs = record.await_args.kwargs
        assert kwargs["kind"] == AgentEventKind.CALL_ENDED.value
        assert kwargs["organization_id"] == 7
        assert kwargs["workflow_id"] == 3 and kwargs["workflow_run_id"] == 77
        assert kwargs["summary"] == "Call · 3m12s · booked"
        assert kwargs["payload"]["duration_seconds"] == 192
        assert kwargs["payload"]["has_recording"] is True

    async def test_a_text_chat_run_writes_no_row(self):
        # Channel replies and direct messages write their own message rows;
        # a "Call · 0m00s" under each would be a lie.
        with (
            patch(
                "api.services.workflow.agent_timeline.db_client.get_workflow_run",
                new=AsyncMock(return_value=_run(mode="textchat")),
            ),
            patch(
                "api.services.workflow.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            await agent_timeline.record_call_ended(77)
        record.assert_not_awaited()

    async def test_the_duration_falls_back_to_the_clock(self):
        with (
            patch(
                "api.services.workflow.agent_timeline.db_client.get_workflow_run",
                new=AsyncMock(return_value=_run(billable_seconds=None)),
            ),
            patch(
                "api.services.workflow.agent_timeline.db_client.get_organization_id_by_workflow_run_id",
                new=AsyncMock(return_value=7),
            ),
            patch(
                "api.services.workflow.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            await agent_timeline.record_call_ended(77)
        assert record.await_args.kwargs["payload"]["duration_seconds"] == 192

    async def test_a_failure_never_reaches_the_completion_job(self):
        with patch(
            "api.services.workflow.agent_timeline.db_client.get_workflow_run",
            new=AsyncMock(side_effect=RuntimeError("down")),
        ):
            await agent_timeline.record_call_ended(77)  # no raise


@pytest.mark.asyncio
class TestTalkingToABotDirectly:
    async def test_a_direct_message_is_recorded_off_channel_and_handed_to_the_bot(self):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        workflow = SimpleNamespace(id=3, name="Front desk", folder_id=5)
        try:
            with (
                patch(
                    "api.routes.agent_timeline.db_client.get_workflow",
                    new=AsyncMock(return_value=workflow),
                ),
                patch(
                    "api.routes.agent_timeline.agent_timeline.record", new=AsyncMock()
                ) as record,
                patch(
                    "api.routes.agent_timeline.enqueue_job", new=AsyncMock()
                ) as enqueue,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/timeline/message",
                        json={"workflow_id": 3, "text": "Book Meera at 4"},
                    )
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert response.status_code == 200, response.text
        assert response.json()["asked"] == [3]
        kwargs = record.await_args.kwargs
        assert kwargs["workflow_id"] == 3 and kwargs["in_channel"] is False
        assert kwargs["payload"]["direct"] is True
        # The bot is asked with no channel: the reply path then reads the
        # bot's own thread for context and files nothing in a channel.
        assert enqueue.await_args.args[1:] == (3, None, "Book Meera at 4", None)

    async def test_it_must_be_a_channel_or_a_bot_not_both(self):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                both = await client.post(
                    "/api/v1/timeline/message",
                    json={"workflow_id": 3, "folder_id": 5, "text": "hi"},
                )
                neither = await client.post(
                    "/api/v1/timeline/message", json={"text": "hi"}
                )
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert both.status_code == 422 and neither.status_code == 422
