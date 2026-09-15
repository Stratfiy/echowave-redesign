"""Asking memory from the Home thread (B1): labelled, billed, never gated.

The eval scenario the brief asks for (``memory_recall_inferred_is_said_not_stated``)
lives at the bottom as scripted turns against Decibyl with the judge's
phrase checks, the same shape as the document scenarios.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.billing import events as billing_events
from api.services.evals import judge
from api.services.knowledge_graph import recall
from api.services.knowledge_graph.client import Fact
from api.services.workflow import connected_tools, decibyl

ORG = 7
AT = datetime(2026, 9, 3, tzinfo=UTC)


class TestDates:
    def test_a_day_is_the_start_of_that_day(self):
        assert recall.parse_day("2026-09-03") == datetime(2026, 9, 3, tzinfo=UTC)

    def test_an_until_day_reaches_its_end(self):
        end = recall.parse_day("2026-09-03", end=True)
        assert end.date().isoformat() == "2026-09-03"
        assert end.hour == 23 and end.minute == 59

    def test_nonsense_is_ignored_not_an_error(self):
        assert recall.parse_day("last month") is None
        assert recall.parse_day(None) is None


class TestTheTool:
    async def test_no_graph_is_unavailable_and_costs_nothing(self):
        charge = AsyncMock()
        with (
            patch.object(recall, "search_facts", AsyncMock(return_value=None)),
            patch.object(billing_events, "charge_in_own_session", charge),
        ):
            out = await recall.for_thread(ORG, {"question": "Ravi"}, ref_id="r1")
        assert out["status"] == "unavailable"
        charge.assert_not_awaited()

    async def test_a_read_that_reached_the_graph_is_a_knowledge_answer(self):
        charge = AsyncMock()
        facts = [Fact("a", "Ravi said he would pay by Friday", AT, None, AT, ())]
        with (
            patch.object(recall, "search_facts", AsyncMock(return_value=facts)),
            patch.object(billing_events, "charge_in_own_session", charge),
        ):
            out = await recall.for_thread(
                ORG, {"question": "Ravi payment"}, ref_id="r1"
            )
        assert out["status"] == "success"
        assert out["facts"][0]["status"] == "inferred"
        assert out["facts"][0]["from"] == "2026-09-03"
        charge.assert_awaited_once()
        assert charge.await_args.kwargs["event"] == billing_events.KNOWLEDGE_ANSWER
        assert charge.await_args.kwargs["ref_id"] == "r1"
        assert billing_events.credits_for(billing_events.KNOWLEDGE_ANSWER) == 2

    async def test_an_empty_answer_is_still_a_read(self):
        charge = AsyncMock()
        with (
            patch.object(recall, "search_facts", AsyncMock(return_value=[])),
            patch.object(billing_events, "charge_in_own_session", charge),
        ):
            out = await recall.for_thread(ORG, {"question": "x"}, ref_id="r2")
        assert out["status"] == "success" and out["facts"] == []
        charge.assert_awaited_once()

    async def test_the_window_and_the_subject_reach_the_search(self):
        search = AsyncMock(return_value=[])
        with (
            patch.object(recall, "search_facts", search),
            patch.object(billing_events, "charge_in_own_session", AsyncMock()),
        ):
            await recall.for_thread(
                ORG,
                {
                    "question": "what about the payment",
                    "about": "Ravi",
                    "since": "2026-08-01",
                    "until": "2026-08-31",
                },
                ref_id="r3",
            )
        args, kwargs = search.await_args
        assert args[0] == ORG and args[1].startswith("Ravi")
        assert kwargs["since"] == datetime(2026, 8, 1, tzinfo=UTC)
        assert kwargs["until"].date().isoformat() == "2026-08-31"

    async def test_nothing_to_look_for_is_an_error_not_a_charge(self):
        charge = AsyncMock()
        with patch.object(billing_events, "charge_in_own_session", charge):
            out = await recall.for_thread(ORG, {}, ref_id="r4")
        assert out["status"] == "error"
        charge.assert_not_awaited()

    async def test_a_search_that_throws_is_unavailable_not_a_crash(self):
        with (
            patch.object(
                recall, "search_facts", AsyncMock(side_effect=RuntimeError("x"))
            ),
            patch.object(billing_events, "charge_in_own_session", AsyncMock()),
        ):
            out = await recall.for_thread(ORG, {"question": "x"}, ref_id="r5")
        assert out["status"] == "unavailable"


class TestItIsOnTheThread:
    def test_recall_is_one_of_decibyls_tools(self):
        assert recall.TOOL_NAME in {t["name"] for t in decibyl.office_tools()}

    def test_recall_is_a_read_so_the_model_may_still_act_after_it(self):
        call = SimpleNamespace(name=recall.TOOL_NAME, id="c")
        assert decibyl._was_a_read(call, {"status": "success", "facts": []})


# The eval scenario: a scripted person, the model stood in, the judge's
# phrase checks on what Decibyl said.


def _session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@contextmanager
def _thread(last_line: str):
    with ExitStack() as stack:
        for p in (
            patch(
                "api.services.workflow.decibyl.build_context",
                new=AsyncMock(return_value="## Team\nnothing"),
            ),
            patch(
                "api.services.workflow.decibyl.office_context",
                new=AsyncMock(return_value=""),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.agent_events",
                new=AsyncMock(
                    return_value=[
                        SimpleNamespace(
                            actor="human",
                            payload={"body": last_line},
                            summary=last_line,
                            at=datetime.now(UTC),
                        )
                    ]
                ),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.async_session",
                return_value=_session(),
            ),
            patch(
                "api.services.agent_builder.settings.resolve_model",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        provider="openai", model="m", api_key="k"
                    )
                ),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ),
            patch("api.services.workflow.decibyl.reply_draft.clear", new=AsyncMock()),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
        ):
            stack.enter_context(p)
        yield


def _transcript(user: str, agent: str) -> list[dict]:
    return [{"role": "user", "text": user}, {"role": "agent", "text": agent}]


@pytest.mark.asyncio
class TestEvalScenarios:
    async def test_memory_recall_inferred_is_said_not_stated(self):
        """The person asks what Ravi said about the payment last month. Recall
        runs with the window, is billed once as a knowledge answer, and the
        answer says the inferred fact as something said, with when, and the
        confirmed one as fact."""
        ask = "what did Ravi say about the payment in August?"
        turn1 = ModelReply(
            text="",
            tool_calls=(
                ToolCall(
                    id="c1",
                    name=recall.TOOL_NAME,
                    arguments={
                        "question": "payment",
                        "about": "Ravi",
                        "since": "2026-08-01",
                        "until": "2026-08-31",
                    },
                ),
            ),
        )
        turn2 = ModelReply(
            text=(
                "On 12 August Ravi said he would clear the pending amount by the "
                "end of that week; that is what he said on the call, not yet "
                "confirmed. Ravi's GST number, 29ABCDE1234F1Z5, is confirmed on "
                "file. Want me to set a reminder to check with him?"
            )
        )
        stream = AsyncMock(side_effect=[turn1, turn2])
        facts = [
            Fact(
                "a",
                "Ravi said he would clear the pending amount by the end of the week",
                datetime(2026, 8, 12, tzinfo=UTC),
                None,
                datetime(2026, 8, 12, tzinfo=UTC),
                (),
            ),
            Fact(
                "b",
                "Ravi's GST number is 29ABCDE1234F1Z5",
                datetime(2026, 8, 2, tzinfo=UTC),
                None,
                datetime(2026, 8, 2, tzinfo=UTC),
                (),
                status="confirmed",
            ),
        ]
        search = AsyncMock(return_value=facts)
        charge = AsyncMock()
        with (
            _thread(ask),
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(recall, "search_facts", search),
            patch.object(billing_events, "charge_in_own_session", charge),
        ):
            body = await decibyl.answer(ORG, ask)
        assert search.await_args.kwargs["since"] == datetime(2026, 8, 1, tzinfo=UTC)
        assert charge.await_count == 1
        assert charge.await_args.kwargs["event"] == billing_events.KNOWLEDGE_ANSWER
        verdict = judge.phrase_checks(
            _transcript(ask, body),
            must_say=["Ravi said", "August", "not yet confirmed"],
            must_not_say=["Ravi will definitely", "reminder set"],
        )
        assert verdict is None, verdict

    async def test_memory_recall_without_a_graph_says_so(self):
        """An Everyday account on a deployment with no graph: recall says
        memory is off, nothing is charged, and Decibyl still answers."""
        ask = "what did Ravi say about the payment?"
        turn1 = ModelReply(
            text="",
            tool_calls=(
                ToolCall(
                    id="c1", name=recall.TOOL_NAME, arguments={"question": "Ravi"}
                ),
            ),
        )
        turn2 = ModelReply(
            text="Memory is not switched on for this workspace yet, so I cannot look back at calls. From the context I have nothing on Ravi."
        )
        charge = AsyncMock()
        with (
            _thread(ask),
            patch(
                "api.services.agent_builder.client.stream",
                new=AsyncMock(side_effect=[turn1, turn2]),
            ),
            patch.object(recall, "search_facts", AsyncMock(return_value=None)),
            patch.object(billing_events, "charge_in_own_session", charge),
        ):
            body = await decibyl.answer(ORG, ask)
        charge.assert_not_awaited()
        verdict = judge.phrase_checks(
            _transcript(ask, body), must_say=["not switched on"], must_not_say=[]
        )
        assert verdict is None, verdict
