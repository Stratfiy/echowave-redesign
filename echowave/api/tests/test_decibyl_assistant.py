"""Decibyl answers on its own thread, from the workspace's own readings.

Arrival tests: a line to Decibyl is a row a screen can render and a job the
worker runs; a mentioned bot is asked in its own chat; the answer is a row
too; the context is built from the team, the memory, the timeline and the
documents, and each reading failing leaves the other three standing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import decibyl
from api.tasks.function_names import FunctionNames


def _workflow(id: int, name: str, handle: str):
    return SimpleNamespace(id=id, name=name, handle=handle)


@pytest.mark.asyncio
class TestAsking:
    async def test_the_line_is_a_row_with_no_bot_and_no_channel_and_a_job(self):
        with (
            patch(
                "api.services.workflow.decibyl.db_client.get_all_workflows_for_listing",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ) as record,
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            asked = await decibyl.ask(
                organization_id=7,
                user_id=42,
                text="what happened this week?",
                attachments=[],
                line="what happened this week?",
                preset=None,
            )
        assert asked == []
        row = record.await_args.kwargs
        assert row["kind"] == AgentEventKind.MESSAGE.value
        assert row["actor"] == AgentEventActor.HUMAN.value
        assert "workflow_id" not in row and "folder_id" not in row
        assert row["payload"]["to"] == "Decibyl"
        assert enqueue.await_args.args[0] == FunctionNames.ANSWER_DECIBYL_MESSAGE
        assert enqueue.await_args.args[1:3] == (7, "what happened this week?")

    async def test_a_mentioned_bot_is_asked_in_its_own_chat(self):
        with (
            patch(
                "api.services.workflow.decibyl.db_client.get_all_workflows_for_listing",
                new=AsyncMock(return_value=[_workflow(3, "Front desk", "front-desk")]),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            asked = await decibyl.ask(
                organization_id=7,
                user_id=42,
                text="@front-desk what did you book today",
                attachments=[],
                line="@front-desk what did you book today",
                preset=None,
            )
        assert asked == [3]
        calls = [c.args for c in enqueue.await_args_list]
        assert (FunctionNames.ANSWER_CHANNEL_MESSAGE, 3, None) == calls[0][:3]
        assert calls[1][0] == FunctionNames.ANSWER_DECIBYL_MESSAGE
        assert calls[1][3] == [3]


class TestTheContext:
    def test_team_lines_are_numbers_the_model_can_quote(self):
        block = decibyl.team_block(
            {
                "agents": 2,
                "live": 1,
                "calls": 9,
                "answered": 7,
                "outcomes": 3,
                "needs_attention": 1,
            },
            [
                {
                    "name": "Quiet",
                    "is_live": False,
                    "calls": 1,
                    "answered": 0,
                    "outcomes": 0,
                    "failures": 0,
                    "status": "",
                },
                {
                    "name": "Front desk",
                    "is_live": True,
                    "calls": 8,
                    "answered": 7,
                    "outcomes": 3,
                    "failures": 2,
                    "status": "2 failed",
                },
            ],
            24,
        )
        assert block.startswith("Team today: 2 bots, 1 live, 9 calls, 7 answered")
        # Busiest first, paused said plainly.
        assert block.index("Front desk") < block.index("Quiet")
        assert "- Quiet: paused; 1 calls" in block

    def test_memory_and_knowledge_say_when_they_are_empty(self):
        assert decibyl.memory_block([]) == "Nothing confirmed yet."
        assert (
            decibyl.knowledge_block({"chunks": []})
            == "No matching passage in Company knowledge."
        )
        assert "(price list) A 5 kg parcel" in decibyl.knowledge_block(
            {
                "chunks": [
                    {
                        "text": "A 5 kg parcel to Chennai costs 320",
                        "document_name": "price list",
                    }
                ]
            }
        )


@pytest.mark.asyncio
class TestAnswering:
    async def test_the_reply_is_a_row_on_the_thread_and_the_model_saw_the_context(self):
        from api.services.agent_builder.client import ModelReply

        reply = ModelReply(text="Front desk took 8 calls, 7 answered.")
        complete = AsyncMock(return_value=reply)
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "api.services.workflow.decibyl.build_context",
                new=AsyncMock(return_value="## Team\nFront desk: 8 calls"),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.agent_events",
                new=AsyncMock(
                    return_value=[
                        SimpleNamespace(
                            actor="human",
                            payload={"body": "what happened today?"},
                            summary="what happened today?",
                            at=datetime.now(UTC),
                        )
                    ]
                ),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.async_session",
                return_value=session,
            ),
            patch(
                "api.services.agent_builder.settings.resolve_model",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        provider="openai", model="m", api_key="k"
                    )
                ),
            ),
            patch("api.services.agent_builder.client.stream", new=complete),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            body = await decibyl.answer(7, "what happened today?")
        assert body == "Front desk took 8 calls, 7 answered."
        sent = complete.await_args.kwargs["conversation"].messages
        # The question travels once, with the context in front of it.
        assert len(sent) == 1
        assert sent[0]["content"].startswith("## Team")
        assert sent[0]["content"].endswith("## Question\nwhat happened today?")
        row = record.await_args.kwargs
        assert row["actor"] == AgentEventActor.AGENT.value
        assert row["payload"]["from"] == "Decibyl"
        assert "workflow_id" not in row

    async def test_a_model_failure_still_leaves_a_line_on_the_thread(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "api.services.workflow.decibyl.build_context",
                new=AsyncMock(return_value=""),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.agent_events",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.async_session",
                return_value=session,
            ),
            patch(
                "api.services.agent_builder.settings.resolve_model",
                new=AsyncMock(side_effect=RuntimeError("no key")),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            body = await decibyl.answer(7, "hello")
        assert "This is us, not you" in body
        assert record.await_args.kwargs["payload"]["body"] == body

    async def test_each_reading_fails_alone(self):
        with (
            patch(
                "api.routes.team._members",
                new=AsyncMock(side_effect=RuntimeError("db")),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.organisation_memory",
                new=AsyncMock(
                    return_value=[
                        SimpleNamespace(
                            key="Opening hours", value="9 to 6", kind="fact"
                        )
                    ]
                ),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.agent_events",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl._knowledge",
                new=AsyncMock(return_value={"chunks": []}),
            ),
        ):
            context = await decibyl.build_context(7, "when do we open?")
        assert "Team today: 0 bots" in context
        assert "- Opening hours: 9 to 6" in context
        assert "No matching passage" in context
