"""Chat memory: a token budget from the plan, a window filled newest-first,
and a meter that does the same arithmetic as the reply."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.billing import plan_limits
from api.services.workflow import chat_memory


class TestTheEstimate:
    def test_four_characters_a_token_and_never_zero_for_words(self):
        assert chat_memory.tokens_of("") == 0
        assert chat_memory.tokens_of(None) == 0
        assert chat_memory.tokens_of("ok") == 1
        assert chat_memory.tokens_of("x" * 400) == 100
        assert chat_memory.tokens_of("x" * 401) == 101


class TestTheWindow:
    def test_fills_from_the_newest_back_and_stops_at_the_first_that_does_not_fit(self):
        newest_first = ["x" * 40, "x" * 40, "x" * 400, "x" * 40]
        kept, used = chat_memory.window(newest_first, budget_tokens=25)
        assert (kept, used) == (2, 20)

    def test_the_newest_message_always_counts_even_alone_over_budget(self):
        kept, used = chat_memory.window(["x" * 4_000], budget_tokens=10)
        assert (kept, used) == (1, 1_000)

    def test_an_empty_thread_is_empty(self):
        assert chat_memory.window([], budget_tokens=100) == (0, 0)


class TestThePlanLadder:
    def test_every_plan_has_a_memory_and_it_grows_up_the_ladder(self):
        figures = [
            plan_limits.SEED[code]["chat_context_tokens"] for code in plan_limits.LADDER
        ]
        assert all(isinstance(f, int) and f > 0 for f in figures)
        assert figures == sorted(figures)
        assert figures[0] < figures[-1]

    def test_the_cap_is_in_the_registry_so_an_operator_can_move_it(self):
        assert "chat_context_tokens" in plan_limits.LIMITS_BY_KEY


@pytest.mark.asyncio
class TestTheBudget:
    async def test_reads_the_plan_and_names_the_next_rung(self):
        limit = SimpleNamespace(value=16_000, plan_code="everyday", raise_to="business")
        with (
            patch(
                "api.services.workflow.chat_memory.db_client.async_session"
            ) as session,
            patch(
                "api.services.billing.plan_limits.limit_for_organization",
                new=AsyncMock(return_value=limit),
            ),
        ):
            session.return_value.__aenter__.return_value = object()
            budget = await chat_memory.budget(7)
        assert budget == chat_memory.Budget(16_000, "everyday", "business")

    async def test_unlimited_means_the_biggest_rung(self):
        limit = SimpleNamespace(value=None, plan_code="scale", raise_to=None)
        with (
            patch(
                "api.services.workflow.chat_memory.db_client.async_session"
            ) as session,
            patch(
                "api.services.billing.plan_limits.limit_for_organization",
                new=AsyncMock(return_value=limit),
            ),
        ):
            session.return_value.__aenter__.return_value = object()
            budget = await chat_memory.budget(7)
        assert budget.tokens == plan_limits.SEED["scale"]["chat_context_tokens"]

    async def test_a_database_that_cannot_be_read_narrows_rather_than_widens(self):
        with patch(
            "api.services.workflow.chat_memory.db_client.async_session",
            side_effect=RuntimeError("down"),
        ):
            budget = await chat_memory.budget(7)
        assert budget.tokens == chat_memory.FALLBACK_TOKENS


@pytest.mark.asyncio
class TestTheMeter:
    async def test_counts_what_the_window_keeps_out_of_the_budget(self):
        rows = [
            SimpleNamespace(payload={"body": "x" * 40}, summary=""),
            SimpleNamespace(payload={}, summary="y" * 40),
            SimpleNamespace(payload={"body": "z" * 4_000}, summary=""),
        ]
        with (
            patch(
                "api.services.workflow.chat_memory.budget",
                new=AsyncMock(return_value=chat_memory.Budget(100, "free", "everyday")),
            ),
            patch(
                "api.services.workflow.chat_memory.db_client.agent_events",
                new=AsyncMock(return_value=rows),
            ) as events,
        ):
            usage = await chat_memory.usage(7, assistant=True)
        assert usage.as_dict() == {
            "used_tokens": 20,
            "budget_tokens": 100,
            "messages_kept": 2,
            "messages_total": 3,
            "plan_code": "free",
            "raise_to": "everyday",
        }
        assert events.await_args.kwargs["assistant_thread"] is True
        assert events.await_args.kwargs["organization_id"] == 7

    async def test_a_bots_chat_is_filtered_by_the_bot(self):
        with (
            patch(
                "api.services.workflow.chat_memory.budget",
                new=AsyncMock(return_value=chat_memory.Budget(100, "free", None)),
            ),
            patch(
                "api.services.workflow.chat_memory.db_client.agent_events",
                new=AsyncMock(return_value=[]),
            ) as events,
        ):
            await chat_memory.usage(7, workflow_id=3)
        assert events.await_args.kwargs["workflow_id"] == 3
        assert "assistant_thread" not in events.await_args.kwargs


@pytest.mark.asyncio
class TestDecibylRemembersAsFarAsThePlanAllows:
    async def test_the_window_is_the_budget_not_ten_turns(self):
        from api.enums import AgentEventActor, AgentEventKind
        from api.services.workflow import decibyl

        def row(i, actor):
            return SimpleNamespace(
                actor=actor,
                kind=AgentEventKind.MESSAGE.value,
                payload={"body": f"line {i:03d} " + "x" * 30},
                summary="",
            )

        rows = [
            row(i, AgentEventActor.HUMAN.value if i % 2 else "bot")
            for i in range(40, 0, -1)
        ]  # newest first: line 040 ... line 001
        with (
            patch(
                "api.services.workflow.decibyl.chat_memory.budget",
                new=AsyncMock(return_value=chat_memory.Budget(60, "free", "everyday")),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.agent_events",
                new=AsyncMock(return_value=rows),
            ),
        ):
            history = await decibyl._history(7)
        # Each line is 39 chars = 10 tokens; 60 tokens keeps six, oldest first.
        assert [h["content"][:8] for h in history] == [
            "line 035",
            "line 036",
            "line 037",
            "line 038",
            "line 039",
            "line 040",
        ]
        assert history[-1]["role"] == "assistant"
