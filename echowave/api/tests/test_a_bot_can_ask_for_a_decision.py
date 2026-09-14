"""A bot stops to ask; a person answers on the card; the bot carries on.

Arrival tests, in the sense this repo uses the word: the question reaches a
row a screen can render, the answer reaches the same row, and the bot is
handed the answer. Each of the three had a way to be built and never wired.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.enums import AgentEventKind
from api.services.workflow import decisions
from api.services.workflow.pipecat_engine_context_composer import (
    compose_functions_for_node,
)


def node():
    return SimpleNamespace(
        out_edges=[],
        document_uuids=[],
        tool_uuids=[],
        mcp_tool_filters=None,
        is_end=False,
    )


class TestWhatCanBeAsked:
    def test_approve_mode_always_offers_approve_and_reject(self):
        payload = decisions.normalise(
            {
                "question": "Refund Rs 1,200 to Meera?",
                "mode": "approve",
                "options": ["yes"],
            }
        )
        assert payload["options"] == ["Approve", "Reject"]
        assert payload["allow_other"] is False

    def test_fewer_than_two_options_is_not_a_question(self):
        assert decisions.normalise({"question": "Go?", "options": ["yes"]}) is None
        assert decisions.normalise({"question": "", "options": ["a", "b"]}) is None

    def test_options_are_deduplicated_and_capped(self):
        payload = decisions.normalise(
            {
                "question": "Which slot?",
                "options": ["10am", "10am", " 11am ", ""] + [str(i) for i in range(20)],
            }
        )
        assert payload["options"][:2] == ["10am", "11am"]
        assert len(payload["options"]) == decisions.MAX_OPTIONS


@pytest.mark.asyncio
class TestTheQuestionReachesTheTimeline:
    async def test_the_tool_is_offered_on_text_runs_only(self):
        offered = [
            f.name
            for f in await compose_functions_for_node(
                node=node(), custom_tool_manager=None, can_ask_for_decision=True
            )
        ]
        withheld = [
            f.name
            for f in await compose_functions_for_node(
                node=node(), custom_tool_manager=None
            )
        ]
        assert decisions.TOOL_NAME in offered
        assert decisions.TOOL_NAME not in withheld

    async def test_asking_writes_a_needs_decision_row(self):
        with patch(
            "api.services.workflow.decisions.agent_timeline.record", new=AsyncMock()
        ) as record:
            result = await decisions.ask(
                organization_id=7,
                workflow_id=3,
                workflow_run_id=11,
                arguments={
                    "question": "Book the 10am or the 4pm?",
                    "why": "Both are free and the caller had no preference.",
                    "options": ["10am", "4pm"],
                    "mode": "single",
                },
            )
        assert result["status"] == "asked"
        kwargs = record.await_args.kwargs
        assert kwargs["kind"] == AgentEventKind.NEEDS_DECISION.value
        assert kwargs["summary"] == "Book the 10am or the 4pm?"
        assert kwargs["payload"]["options"] == ["10am", "4pm"]
        assert kwargs["workflow_id"] == 3 and kwargs["workflow_run_id"] == 11

    async def test_an_unanswerable_ask_is_reported_not_recorded(self):
        with patch(
            "api.services.workflow.decisions.agent_timeline.record", new=AsyncMock()
        ) as record:
            result = await decisions.ask(
                organization_id=7,
                workflow_id=3,
                workflow_run_id=11,
                arguments={"question": "?", "options": []},
            )
        assert result["status"] == "not_asked"
        record.assert_not_awaited()


def _question(**overrides):
    payload = {
        "question": "Book the 10am or the 4pm?",
        "why": "",
        "options": ["10am", "4pm"],
        "mode": "single",
        "allow_other": False,
    }
    payload.update(overrides)
    return SimpleNamespace(
        id=99,
        kind=AgentEventKind.NEEDS_DECISION.value,
        payload=payload,
        folder_id=5,
        workflow_id=3,
    )


@pytest.mark.asyncio
class TestTheAnswerReachesTheCardAndTheBot:
    async def test_the_answer_is_written_onto_the_question(self):
        with (
            patch(
                "api.services.workflow.decisions.db_client.get_agent_event",
                new=AsyncMock(return_value=_question()),
            ),
            patch(
                "api.services.workflow.decisions.db_client.set_agent_event_payload",
                new=AsyncMock(return_value=True),
            ) as write,
            patch(
                "api.services.workflow.decisions.agent_timeline.record", new=AsyncMock()
            ) as record,
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            payload = await decisions.decide(
                organization_id=7, event_id=99, choice=["4pm"], other=None, user_id=42
            )
        assert payload["decided"]["choice"] == ["4pm"]
        assert payload["decided"]["by"] == 42
        assert write.await_args.kwargs["payload"]["decided"]["choice"] == ["4pm"]
        # The answer is a message in the channel, and the bot is asked to go on.
        message = record.await_args.kwargs
        assert message["kind"] == AgentEventKind.MESSAGE.value
        assert message["folder_id"] == 5
        assert "4pm" in message["summary"]
        assert enqueue.await_args.args[1:3] == (3, 5)

    async def test_a_second_answer_is_refused(self):
        with patch(
            "api.services.workflow.decisions.db_client.get_agent_event",
            new=AsyncMock(return_value=_question(decided={"choice": ["10am"]})),
        ):
            with pytest.raises(decisions.DecisionError, match="Already"):
                await decisions.decide(
                    organization_id=7,
                    event_id=99,
                    choice=["4pm"],
                    other=None,
                    user_id=42,
                )

    async def test_single_mode_takes_one(self):
        with patch(
            "api.services.workflow.decisions.db_client.get_agent_event",
            new=AsyncMock(return_value=_question()),
        ):
            with pytest.raises(decisions.DecisionError, match="Pick one"):
                await decisions.decide(
                    organization_id=7,
                    event_id=99,
                    choice=["10am", "4pm"],
                    other=None,
                    user_id=42,
                )

    async def test_a_written_answer_needs_permission(self):
        with patch(
            "api.services.workflow.decisions.db_client.get_agent_event",
            new=AsyncMock(return_value=_question()),
        ):
            with pytest.raises(decisions.DecisionError, match="written"):
                await decisions.decide(
                    organization_id=7, event_id=99, choice=[], other="2pm", user_id=42
                )

    async def test_the_route_says_why_it_refused(self):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        try:
            with patch(
                "api.services.workflow.decisions.db_client.get_agent_event",
                new=AsyncMock(return_value=_question(decided={"choice": ["10am"]})),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/timeline/decide",
                        json={"event_id": 99, "choice": ["4pm"]},
                    )
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert response.status_code == 409
        assert response.json()["detail"] == "Already answered."
