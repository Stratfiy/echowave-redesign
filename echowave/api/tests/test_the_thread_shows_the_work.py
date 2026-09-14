"""What a bot read on the way to an answer is a line on the thread.

Arrival tests: a knowledge lookup that returned something leaves "Read N
passages from <document>"; an empty lookup leaves nothing; Decibyl's readings
and hand-offs are lines on its own thread and are not fed back to the model
as conversation; the thread filter carries them.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline, decibyl


class TestTheLine:
    def test_one_document_is_named_and_many_are_company_knowledge(self):
        one = {"chunks": [{"text": "a", "document_name": "price list"}] * 3}
        many = {
            "chunks": [
                {"text": "a", "document_name": "price list"},
                {"text": "b", "document_name": "hours"},
            ]
        }
        assert (
            agent_timeline.read_passages_line(one) == "Read 3 passages from price list"
        )
        assert agent_timeline.read_passages_line(many) == (
            "Read 2 passages from Company knowledge"
        )
        assert agent_timeline.read_passages_line({"chunks": [{"text": "a"}]}) == (
            "Read 1 passage from Company knowledge"
        )

    def test_nothing_read_is_no_line(self):
        assert agent_timeline.read_passages_line({"chunks": []}) is None
        assert agent_timeline.read_passages_line({"status": "unavailable"}) is None
        assert agent_timeline.read_passages_line("not a dict") is None

    def test_decibyls_readings_read_as_one_sentence(self):
        assert (
            decibyl.readings_line(bots=2, facts=0, passages=0)
            == "Read the team (2 bots)"
        )
        assert decibyl.readings_line(bots=1, facts=1, passages=3) == (
            "Read the team (1 bot), 1 confirmed fact and 3 passages from Company knowledge"
        )


@pytest.mark.asyncio
class TestTheRowsArrive:
    async def test_a_reading_is_an_activity_row(self):
        with patch(
            "api.services.workflow.agent_timeline.record", new=AsyncMock()
        ) as record:
            await agent_timeline.record_activity(
                organization_id=7,
                summary="Read 3 passages from price list",
                workflow_id=3,
            )
        row = record.await_args.kwargs
        assert row["kind"] == AgentEventKind.ACTIVITY.value
        assert row["workflow_id"] == 3

    async def test_a_hand_off_leaves_asked_on_decibyls_thread(self):
        with (
            patch(
                "api.services.workflow.decibyl.db_client.get_all_workflows_for_listing",
                new=AsyncMock(
                    return_value=[
                        SimpleNamespace(id=3, name="Front desk", handle="front-desk")
                    ]
                ),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record_activity",
                new=AsyncMock(),
            ) as activity,
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
        ):
            await decibyl.ask(
                organization_id=7,
                user_id=42,
                text="@front-desk what did you book",
                attachments=[],
                line="@front-desk what did you book",
                preset=None,
            )
        row = activity.await_args.kwargs
        assert row["summary"] == "Asked Front desk"
        assert row["in_channel"] is False and "workflow_id" not in row

    async def test_the_context_leaves_a_readings_line(self):
        with (
            patch("api.routes.team._members", new=AsyncMock(return_value=[])),
            patch(
                "api.services.workflow.decibyl.db_client.organisation_memory",
                new=AsyncMock(
                    return_value=[SimpleNamespace(key="k", value="v", kind="fact")]
                ),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.agent_events",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.list_missed_calls",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl._knowledge",
                new=AsyncMock(
                    return_value={"chunks": [{"text": "a", "document_name": "d"}]}
                ),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record_activity",
                new=AsyncMock(),
            ) as activity,
        ):
            await decibyl.build_context(7, "hello")
        assert activity.await_args.kwargs["summary"] == (
            "Read the team (0 bots), 1 confirmed fact and 1 passage from Company knowledge"
        )

    async def test_readings_are_on_the_thread_but_not_in_the_models_history(self):
        assert AgentEventKind.ACTIVITY.value in decibyl.thread_filter()["kinds"]
        rows = [
            SimpleNamespace(
                kind="message",
                actor=AgentEventActor.AGENT.value,
                payload={"body": "Front desk took 8 calls."},
                summary="Front desk took 8 calls.",
            ),
            SimpleNamespace(
                kind="activity",
                actor=AgentEventActor.AGENT.value,
                payload={},
                summary="Read the team (2 bots)",
            ),
        ]
        with patch(
            "api.services.workflow.decibyl.db_client.agent_events",
            new=AsyncMock(return_value=rows),
        ):
            history = await decibyl._history(7)
        assert [h["content"] for h in history] == ["Front desk took 8 calls."]
