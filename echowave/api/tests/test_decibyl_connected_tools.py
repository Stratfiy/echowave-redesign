"""Decibyl reaches the workspace's connected apps -- and the office rule holds.

Three things must be true, and they are the three tests: a read runs in
the turn and its result reaches the model's next call; a write is turned
into a run_tool card and is never run from the thread; and a model that
keeps asking for tools is cut off at the round limit rather than left to
spend credit. Then the card itself: resolving names the app, and confirming
runs it through the same layer.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.workflow import actions, connected_tools, decibyl


def _tool(slug: str, name: str = "Gmail: fetch emails", toolkit: str = "gmail"):
    return SimpleNamespace(
        id=1,
        tool_uuid="t-1",
        name=name,
        description="d",
        category="composio",
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": toolkit, "parameters": []},
        },
    )


def _session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _thread_patches():
    """The readings answer() makes around the model, stood in for."""
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
                        payload={"body": "any mail from Meera?"},
                        summary="any mail from Meera?",
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
    """All of the above as one context manager, since a starred tuple is
    not allowed inside a parenthesised ``with``."""
    with ExitStack() as stack:
        for p in _thread_patches():
            stack.enter_context(p)
        yield


@pytest.mark.asyncio
class TestFromTheThread:
    async def test_a_read_runs_in_the_turn_and_feeds_the_answer(self):
        tool = _tool("GMAIL_FETCH_EMAILS")
        fn = connected_tools.function_name(tool)
        asks = ModelReply(
            text="",
            tool_calls=(ToolCall(id="c1", name=fn, arguments={"query": "Meera"}),),
        )
        answers = ModelReply(text="Yes: two mails from Meera this morning.")
        stream = AsyncMock(side_effect=[asks, answers])
        with (
            _thread(),
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[tool])
            ),
            patch.object(
                connected_tools,
                "execute",
                AsyncMock(return_value={"status": "success", "data": {"count": 2}}),
            ) as run,
            patch.object(actions, "propose", AsyncMock()) as propose,
        ):
            body = await decibyl.answer(7, "any mail from Meera?")

        assert body == "Yes: two mails from Meera this morning."
        run.assert_awaited_once()
        assert run.await_args.kwargs["arguments"] == {"query": "Meera"}
        assert run.await_args.kwargs["ref_id"] == "decibyl:7:c1"
        propose.assert_not_awaited()
        # The first call offered the app as a tool; the second saw its result.
        first, second = stream.await_args_list
        assert any(t["name"] == fn for t in first.kwargs["tools"])
        sent = second.kwargs["conversation"].messages
        assert sent[-1]["role"] == "tool" and sent[-1]["content"]["data"] == {
            "count": 2
        }

    async def test_a_write_becomes_a_card_and_is_never_run(self):
        tool = _tool("GMAIL_SEND_EMAIL", name="Gmail: send email")
        fn = connected_tools.function_name(tool)
        asks = ModelReply(
            text="",
            tool_calls=(ToolCall(id="c2", name=fn, arguments={"to": "meera@x"}),),
        )
        answers = ModelReply(text="I have proposed sending that; confirm on the card.")
        stream = AsyncMock(side_effect=[asks, answers])
        with (
            _thread(),
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[tool])
            ),
            patch.object(connected_tools, "execute", AsyncMock()) as run,
            patch.object(
                actions, "propose", AsyncMock(return_value={"status": "proposed"})
            ) as propose,
        ):
            body = await decibyl.answer(7, "email Meera the quote")

        assert "proposed" in body
        run.assert_not_awaited()
        propose.assert_awaited_once()
        args = propose.await_args.kwargs["arguments"]
        assert args["action"] == actions.RUN_TOOL
        assert args["tool_uuid"] == "t-1"
        assert args["arguments"] == {"to": "meera@x"}
        assert propose.await_args.kwargs["in_channel"] is False
        # A card ends the tool phase: the model is given no tools and told to
        # say it has proposed, so it cannot propose the same thing twice.
        assert stream.await_args_list[-1].kwargs["tools"] is None

    async def test_a_model_that_keeps_asking_is_cut_off_at_the_round_limit(self):
        tool = _tool("GMAIL_FETCH_EMAILS")
        fn = connected_tools.function_name(tool)
        asks = ModelReply(
            text="", tool_calls=(ToolCall(id="c", name=fn, arguments={}),)
        )
        final = ModelReply(text="Here is what I found.")
        stream = AsyncMock(side_effect=[asks] * decibyl.MAX_TOOL_ROUNDS + [final])
        with (
            _thread(),
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[tool])
            ),
            patch.object(
                connected_tools,
                "execute",
                AsyncMock(return_value={"status": "success", "data": []}),
            ) as run,
        ):
            body = await decibyl.answer(7, "keep looking")

        assert body == "Here is what I found."
        assert run.await_count == decibyl.MAX_TOOL_ROUNDS
        assert stream.await_count == decibyl.MAX_TOOL_ROUNDS + 1
        # The last call withholds the tools, which is what ends the loop.
        assert stream.await_args_list[-1].kwargs["tools"] is None

    async def test_an_app_that_is_not_connected_is_unavailable_not_an_error(self):
        call = ToolCall(id="c", name="app_something_nobody_connected", arguments={})
        with patch.object(
            connected_tools, "list_for_organization", AsyncMock(return_value=[])
        ):
            result = await decibyl._tool(7, call)
        assert result["status"] == "unavailable"


@pytest.mark.asyncio
class TestTheCard:
    async def test_resolving_names_the_app_and_is_not_reversible(self):
        tool = _tool("GMAIL_SEND_EMAIL", name="Gmail: send email")
        with patch.object(
            actions.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
        ) as lookup:
            payload = await actions.resolve(
                organization_id=7,
                workflow_id=None,
                arguments={
                    "action": actions.RUN_TOOL,
                    "tool_uuid": "t-1",
                    "arguments": {"to": "meera@x"},
                    "why": "asked",
                },
            )
        # Tenant-scoped lookup: the org comes from the caller, never the text.
        assert lookup.await_args.kwargs["organization_id"] == 7
        assert payload["label"] == "Gmail: send email via gmail"
        assert payload["reversible"] is False
        assert payload["args"]["arguments"] == {"to": "meera@x"}

    async def test_an_unconnected_or_foreign_tool_cannot_be_proposed(self):
        with (
            patch.object(
                actions.db_client, "get_tool_by_uuid", AsyncMock(return_value=None)
            ),
            pytest.raises(actions.ActionError),
        ):
            await actions.resolve(
                organization_id=7,
                workflow_id=None,
                arguments={
                    "action": actions.RUN_TOOL,
                    "tool_uuid": "t-9",
                    "why": "",
                },
            )

    async def test_run_tool_is_not_in_the_model_facing_enum(self):
        # Decibyl reaches an app through the app's own function; the generic
        # propose_action never offers "run any tool" to a bot.
        assert actions.RUN_TOOL not in actions.tool_properties()["action"]["enum"]

    async def test_confirming_runs_it_through_the_same_layer(self):
        tool = _tool("GMAIL_SEND_EMAIL", name="Gmail: send email")
        payload = {
            "action": actions.RUN_TOOL,
            "args": {
                "tool_uuid": "t-1",
                "tool_name": "Gmail: send email",
                "arguments": {"to": "m"},
            },
            "confirmed": {"by": 3, "at": "2026-09-15T08:00:00Z"},
        }
        with (
            patch.object(
                actions.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
            ),
            patch.object(
                connected_tools,
                "execute",
                AsyncMock(return_value={"status": "success", "data": {"id": "m1"}}),
            ) as run,
        ):
            line = await actions._execute(7, payload)
        assert line.startswith("Done: Gmail: send email")
        assert run.await_args.kwargs["arguments"] == {"to": "m"}
        assert run.await_args.kwargs["ref_id"].startswith("run_tool:7:t-1:")
        assert payload["result"]["data"] == {"id": "m1"}

    async def test_a_failed_run_is_a_refusal_on_the_card(self):
        tool = _tool("GMAIL_SEND_EMAIL")
        with (
            patch.object(
                actions.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
            ),
            patch.object(
                connected_tools,
                "execute",
                AsyncMock(return_value={"status": "error", "error": "rejected"}),
            ),
            pytest.raises(actions.ActionError, match="rejected"),
        ):
            await actions._execute(
                7, {"action": actions.RUN_TOOL, "args": {"tool_uuid": "t-1"}}
            )
