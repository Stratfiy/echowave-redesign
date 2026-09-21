"""D-1a: Decibyl keeps working past a reply's tool-round cap.

With the flag off a turn that hits the cap answers with what it has, as
before. With it on, the turn hands the rest to a task on the board -- the
transcript and tool state travel with it -- and a worker carries on from
there: more rounds under a ceiling, a progress line every few steps, the
spend cap checked as it goes, the answer on the thread and on the card.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.workflow import (
    connected_tools,
    decibyl,
    decibyl_tasks,
    tasks_board,
)


def _tool(slug: str = "GMAIL_FETCH_EMAILS", name: str = "Gmail: fetch emails"):
    return SimpleNamespace(
        id=1,
        tool_uuid="t-1",
        name=name,
        description="d",
        category="composio",
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": "gmail", "parameters": []},
        },
    )


def _session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _thread_patches():
    return (
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
                        payload={"body": "find every unpaid invoice"},
                        summary="find every unpaid invoice",
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
                return_value=SimpleNamespace(provider="openai", model="m", api_key="k")
            ),
        ),
        patch("api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()),
        patch("api.services.workflow.decibyl.reply_draft.clear", new=AsyncMock()),
    )


@contextmanager
def _thread():
    with ExitStack() as stack:
        for p in _thread_patches():
            stack.enter_context(p)
        yield


def _asks(fn: str, i: int) -> ModelReply:
    return ModelReply(
        text="", tool_calls=(ToolCall(id=f"c{i}", name=fn, arguments={"page": i}),)
    )


@pytest.mark.asyncio
class TestAtTheCap:
    async def test_flag_off_the_turn_ends_with_what_it_has(self, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_LONG_TASKS_ENABLED", False)
        tool = _tool()
        fn = connected_tools.function_name(tool)
        asks = [_asks(fn, i) for i in range(decibyl.MAX_TOOL_ROUNDS)]
        stream = AsyncMock(side_effect=[*asks, ModelReply(text="Here is what I read.")])
        with (
            _thread(),
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[tool])
            ),
            patch.object(
                connected_tools,
                "execute",
                AsyncMock(return_value={"status": "success", "data": {}}),
            ),
            patch.object(decibyl_tasks, "hand_off", AsyncMock()) as hand_off,
        ):
            body = await decibyl.answer(7, "find every unpaid invoice")
        assert body == "Here is what I read."
        hand_off.assert_not_awaited()
        # The last call carried the notice and no tools.
        last = stream.await_args_list[-1]
        assert last.kwargs["tools"] is None
        assert last.kwargs["conversation"].messages[-1]["content"] == (
            decibyl.LAST_STEP_NOTICE
        )

    async def test_flag_on_the_rest_goes_to_the_board(self, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_LONG_TASKS_ENABLED", True)
        tool = _tool()
        fn = connected_tools.function_name(tool)
        asks = [_asks(fn, i) for i in range(decibyl.MAX_TOOL_ROUNDS + 1)]
        stream = AsyncMock(side_effect=asks)
        filed = SimpleNamespace(id=42, title="find every unpaid invoice")
        with (
            _thread(),
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[tool])
            ),
            patch.object(
                connected_tools,
                "execute",
                AsyncMock(return_value={"status": "success", "data": {}}),
            ),
            patch.object(
                decibyl_tasks, "hand_off", AsyncMock(return_value=filed)
            ) as hand_off,
        ):
            body = await decibyl.answer(
                7, "find every unpaid invoice", thread_id="t-9", author_id=3
            )
        assert "task #42" in body
        assert f"{decibyl.MAX_TOOL_ROUNDS} steps" in body
        hand_off.assert_awaited_once()
        kwargs = hand_off.await_args.kwargs
        assert kwargs["thread_id"] == "t-9"
        assert kwargs["author_id"] == 3
        assert kwargs["request"] == "find every unpaid invoice"
        assert kwargs["rounds"] == decibyl.MAX_TOOL_ROUNDS
        # The whole transcript travels, tool results included.
        assert kwargs["messages"][-1]["role"] == "tool"
        assert sum(1 for m in kwargs["messages"] if m["role"] == "tool") == (
            decibyl.MAX_TOOL_ROUNDS
        )
        # The model was never asked to wrap up.
        assert len(stream.await_args_list) == decibyl.MAX_TOOL_ROUNDS
        assert all(
            m["content"] != decibyl.LAST_STEP_NOTICE
            for m in kwargs["messages"]
            if m["role"] == "user"
        )


class _Board:
    """A stand-in for the task row and the client that reads and writes it."""

    def __init__(self, continuation: dict):
        self.task = SimpleNamespace(
            id=42,
            organization_id=7,
            title="find every unpaid invoice",
            brief="find every unpaid invoice",
            status=tasks_board.TODO,
            continuation=continuation,
            result=None,
            from_workflow_id=None,
            assignee_workflow_id=None,
            created_by=3,
            depth=0,
            due_at=None,
            workflow_run_id=None,
            created_at=None,
            started_at=None,
            finished_at=None,
        )
        self.updates: list[dict] = []

    async def get(self, model, task_id):
        return self.task

    async def update_task(self, task_id, *, organization_id, **fields):
        self.updates.append(fields)
        for k, v in fields.items():
            setattr(self.task, k, v)
        return self.task


def _continuation(**overrides) -> dict:
    return {
        "messages": [
            {"role": "user", "content": "## Question\nfind every unpaid invoice"}
        ],
        "loaded": {},
        "preset": None,
        "thread_id": "t-9",
        "author_id": 3,
        "request": "find every unpaid invoice",
        "rounds": 6,
        **overrides,
    }


@contextmanager
def _worker(board: _Board, *, allowed: bool = True):
    session = _session()
    session.get = board.get
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "api.services.workflow.decibyl_tasks.db_client.async_session",
                return_value=session,
            )
        )
        stack.enter_context(
            patch(
                "api.services.workflow.decibyl_tasks.db_client.update_task",
                new=board.update_task,
            )
        )
        stack.enter_context(
            patch(
                "api.services.agent_builder.settings.resolve_choice",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        provider="openai", model="m", api_key="k"
                    )
                ),
            )
        )
        stack.enter_context(
            patch(
                "api.services.billing.budgets.evaluate_in_own_session",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        allowed=allowed,
                        message="The spend cap on the workspace is used up",
                    )
                ),
            )
        )
        stack.enter_context(
            patch(
                "api.services.billing.reservations.has_credit",
                new=AsyncMock(return_value=True),
            )
        )
        record = stack.enter_context(
            patch(
                "api.services.workflow.decibyl_tasks.agent_timeline.record",
                new=AsyncMock(),
            )
        )
        activity = stack.enter_context(
            patch(
                "api.services.workflow.decibyl_tasks.agent_timeline.record_activity",
                new=AsyncMock(),
            )
        )
        stack.enter_context(
            patch("api.services.workflow.decibyl.reply_draft.clear", new=AsyncMock())
        )
        stack.enter_context(
            patch(
                "api.services.workflow.decibyl.reply_draft.set_draft", new=AsyncMock()
            )
        )
        charge = stack.enter_context(
            patch(
                "api.services.billing.events.charge_in_own_session",
                new=AsyncMock(return_value=50),
            )
        )
        yield SimpleNamespace(record=record, activity=activity, charge=charge)


@pytest.mark.asyncio
class TestInTheBackground:
    async def test_it_carries_on_and_the_answer_lands_on_the_thread_and_the_card(
        self, monkeypatch
    ):
        monkeypatch.setattr(constants, "DECIBYL_LONG_TASKS_ENABLED", True)
        monkeypatch.setattr(constants, "DECIBYL_TASK_MAX_ROUNDS", 40)
        monkeypatch.setattr(constants, "DECIBYL_TASK_PROGRESS_EVERY", 5)
        tool = _tool()
        fn = connected_tools.function_name(tool)
        asks = [_asks(fn, i) for i in range(7)]
        stream = AsyncMock(
            side_effect=[*asks, ModelReply(text="Nine invoices are unpaid.")]
        )
        board = _Board(_continuation())
        with (
            _worker(board) as seen,
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[tool])
            ),
            patch.object(
                connected_tools,
                "execute",
                AsyncMock(return_value={"status": "success", "data": {}}),
            ),
        ):
            await decibyl_tasks.continue_task(42)

        assert board.task.status == tasks_board.DONE
        assert board.task.result.startswith("Nine invoices are unpaid.")
        # The answer is a message on the thread, from Decibyl.
        seen.record.assert_awaited_once()
        assert seen.record.await_args.kwargs["summary"] == "Nine invoices are unpaid."
        assert seen.record.await_args.kwargs["payload"]["task_id"] == 42
        # Progress every five steps: 7 more rounds on top of 6 -> one line.
        lines = [c.kwargs["summary"] for c in seen.activity.await_args_list]
        assert any(l.startswith("Still on it: 11 steps") for l in lines)
        assert any(l.startswith("Decibyl finished the task") for l in lines)
        # First call resumed from the carried transcript, with tools.
        first = stream.await_args_list[0]
        assert (
            first.kwargs["conversation"]
            .messages[0]["content"]
            .endswith("find every unpaid invoice")
        )
        assert first.kwargs["tools"]
        seen.charge.assert_awaited_once()
        assert seen.charge.await_args.kwargs["ref_id"] == "decibyl-task:42"
        # The card keeps the answer, not the working.
        assert board.task.continuation["messages"] == []
        assert board.task.continuation["done"] is True

    async def test_out_of_steps_it_is_asked_to_answer_and_the_card_says_so(
        self, monkeypatch
    ):
        monkeypatch.setattr(constants, "DECIBYL_LONG_TASKS_ENABLED", True)
        monkeypatch.setattr(constants, "DECIBYL_TASK_MAX_ROUNDS", 3)
        monkeypatch.setattr(constants, "DECIBYL_TASK_PROGRESS_EVERY", 50)
        tool = _tool()
        fn = connected_tools.function_name(tool)
        # Three rounds of tools are allowed; the fourth call carries no tools
        # and a real model answers in prose, which is what the stub does.
        asks = [_asks(fn, i) for i in range(3)]
        stream = AsyncMock(
            side_effect=[*asks, ModelReply(text="Read three pages; six left.")]
        )
        board = _Board(_continuation())
        with (
            _worker(board),
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[tool])
            ),
            patch.object(
                connected_tools,
                "execute",
                AsyncMock(return_value={"status": "success", "data": {}}),
            ),
        ):
            await decibyl_tasks.continue_task(42)

        assert board.task.status == tasks_board.COULD_NOT
        assert "Read three pages; six left." in board.task.result
        assert decibyl_tasks.RAN_OUT in board.task.result
        # Three rounds of tools, then the finish-now notice with no tools.
        assert len(stream.await_args_list) == 4
        last = stream.await_args_list[-1]
        assert last.kwargs["tools"] is None
        assert (
            decibyl_tasks.RAN_OUT in last.kwargs["conversation"].messages[-1]["content"]
        )

    async def test_a_spend_cap_stops_it_before_it_starts(self, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_LONG_TASKS_ENABLED", True)
        board = _Board(_continuation())
        stream = AsyncMock()
        with (
            _worker(board, allowed=False),
            patch("api.services.agent_builder.client.stream", new=stream),
        ):
            await decibyl_tasks.continue_task(42)
        assert board.task.status == tasks_board.WAITING
        assert "spend cap" in board.task.result
        stream.assert_not_awaited()

    async def test_a_card_ends_the_tool_phase_like_in_the_turn(self, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_LONG_TASKS_ENABLED", True)
        monkeypatch.setattr(constants, "DECIBYL_TASK_MAX_ROUNDS", 40)
        tool = _tool("GMAIL_SEND_EMAIL", name="Gmail: send email")
        fn = connected_tools.function_name(tool)
        stream = AsyncMock(
            side_effect=[
                ModelReply(
                    text="",
                    tool_calls=(ToolCall(id="c", name=fn, arguments={"to": "a@b"}),),
                ),
                ModelReply(text="I have proposed the send; confirm on the card."),
            ]
        )
        board = _Board(_continuation())
        with (
            _worker(board),
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[tool])
            ),
            patch.object(connected_tools, "execute", AsyncMock()) as run,
            patch(
                "api.services.workflow.decibyl.actions.propose",
                AsyncMock(return_value={"status": "proposed"}),
            ) as propose,
            patch(
                "api.services.workflow.decibyl.draft_requests.refusal",
                return_value=None,
            ),
        ):
            await decibyl_tasks.continue_task(42)
        run.assert_not_awaited()
        propose.assert_awaited_once()
        assert board.task.status == tasks_board.DONE
        assert stream.await_args_list[-1].kwargs["tools"] is None

    async def test_flag_off_it_waits(self, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_LONG_TASKS_ENABLED", False)
        board = _Board(_continuation())
        with _worker(board):
            await decibyl_tasks.continue_task(42)
        assert board.task.status == tasks_board.WAITING

    async def test_a_bots_task_is_not_a_continuation(self):
        assert not decibyl_tasks.is_continuation(SimpleNamespace(continuation=None))
        assert decibyl_tasks.is_continuation(
            SimpleNamespace(continuation={"rounds": 1})
        )


class TestTheTitle:
    def test_is_the_ask_on_one_line(self):
        assert decibyl_tasks.title_for("find\nevery   unpaid invoice") == (
            "find every unpaid invoice"
        )

    def test_is_cut_at_the_column(self):
        assert len(decibyl_tasks.title_for("x" * 500)) == decibyl_tasks.TITLE_MAX
