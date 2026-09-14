"""A bot proposes to act; a person confirms; there is time to take it back.

Arrival tests: the proposal is a row a card can render, confirming arms it
with a delay rather than running it, undo inside the window leaves nothing
done, the job that fires re-reads the row first, a reversible action can be
put back after it ran and an irreversible one cannot, and the same tool is
offered to every bot on a staff chat and to Decibyl.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.enums import AgentEventKind
from api.services.workflow import actions
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


def _bot(id: int, name: str, handle: str):
    return SimpleNamespace(id=id, name=name, handle=handle)


def _proposal(payload: dict, *, workflow_id=3, folder_id=5):
    return SimpleNamespace(
        id=99,
        organization_id=7,
        kind=AgentEventKind.ACTION_PROPOSED.value,
        payload=payload,
        workflow_id=workflow_id,
        folder_id=folder_id,
    )


def _switch(state: str, **extra) -> dict:
    return {
        "action": actions.TURN_BOT_ON,
        "args": {"workflow_id": 3, "bot_name": "Front desk", "is_live": True},
        "label": "Turn Front desk on",
        "why": "It has been off since Monday",
        "reversible": True,
        "state": state,
        **extra,
    }


def _callback(state: str) -> dict:
    return {
        "action": actions.RETURN_MISSED_CALL,
        "args": {"missed_call_id": 12, "caller": "+919876543210"},
        "label": "Call +919876543210 back",
        "why": "They rang twice",
        "reversible": False,
        "state": state,
    }


@pytest.mark.asyncio
class TestWhatCanBeProposed:
    async def test_the_tool_is_offered_where_a_person_can_confirm(self):
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
        assert actions.TOOL_NAME in offered
        assert actions.TOOL_NAME not in withheld

    async def test_a_bot_named_by_handle_becomes_a_reversible_switch(self):
        with patch(
            "api.services.workflow.actions.db_client.get_all_workflows_for_listing",
            new=AsyncMock(return_value=[_bot(3, "Front desk", "front-desk")]),
        ):
            payload = await actions.resolve(
                organization_id=7,
                workflow_id=None,
                arguments={"action": "turn_bot_on", "bot": "@Front-Desk", "why": "x"},
            )
        assert payload["args"] == {
            "workflow_id": 3,
            "bot_name": "Front desk",
            "is_live": True,
        }
        assert payload["label"] == "Turn Front desk on"
        assert payload["reversible"] is True
        assert payload["state"] == actions.PROPOSED

    async def test_no_bot_named_means_the_bot_itself(self):
        with patch(
            "api.services.workflow.actions.db_client.get_workflow",
            new=AsyncMock(return_value=_bot(3, "Front desk", "front-desk")),
        ):
            payload = await actions.resolve(
                organization_id=7,
                workflow_id=3,
                arguments={"action": "turn_bot_off", "why": "quiet hours"},
            )
        assert payload["args"]["workflow_id"] == 3
        assert payload["label"] == "Turn Front desk off"

    async def test_an_unknown_bot_is_refused_not_guessed(self):
        with patch(
            "api.services.workflow.actions.db_client.get_all_workflows_for_listing",
            new=AsyncMock(
                return_value=[
                    _bot(3, "Front desk", "front-desk"),
                    _bot(4, "Front", "front"),
                ]
            ),
        ):
            with pytest.raises(actions.ActionError, match="exact name"):
                await actions.resolve(
                    organization_id=7,
                    workflow_id=None,
                    arguments={
                        "action": "turn_bot_on",
                        "bot": "Back office",
                        "why": "x",
                    },
                )

    async def test_a_returned_caller_cannot_be_proposed_again(self):
        with patch(
            "api.services.workflow.actions.db_client.get_missed_call",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    id=12, caller="+919876543210", outcome="called_back"
                )
            ),
        ):
            with pytest.raises(actions.ActionError, match="already"):
                await actions.resolve(
                    organization_id=7,
                    workflow_id=None,
                    arguments={
                        "action": "return_missed_call",
                        "missed_call_id": 12,
                        "why": "x",
                    },
                )

    async def test_a_missed_call_is_an_irreversible_proposal(self):
        with patch(
            "api.services.workflow.actions.db_client.get_missed_call",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    id=12, caller="+919876543210", outcome="pending"
                )
            ),
        ):
            payload = await actions.resolve(
                organization_id=7,
                workflow_id=None,
                arguments={
                    "action": "return_missed_call",
                    "missed_call_id": "12",
                    "why": "x",
                },
            )
        assert payload["reversible"] is False
        assert payload["label"] == "Call +919876543210 back"

    async def test_proposing_writes_a_row_and_tells_the_model_to_stop(self):
        with (
            patch(
                "api.services.workflow.actions.resolve",
                new=AsyncMock(return_value=_switch(actions.PROPOSED)),
            ),
            patch(
                "api.services.workflow.actions.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            told = await actions.propose(
                organization_id=7,
                workflow_id=3,
                workflow_run_id=11,
                arguments={"action": "turn_bot_on", "why": "x"},
            )
        assert told["status"] == "proposed"
        assert "confirm" in told["note"]
        row = record.await_args.kwargs
        assert row["kind"] == AgentEventKind.ACTION_PROPOSED.value
        assert row["summary"] == "Turn Front desk on"
        assert row["payload"]["state"] == actions.PROPOSED

    async def test_a_refused_proposal_is_told_not_recorded(self):
        with patch(
            "api.services.workflow.actions.agent_timeline.record", new=AsyncMock()
        ) as record:
            told = await actions.propose(
                organization_id=7,
                workflow_id=None,
                workflow_run_id=None,
                arguments={"action": "sell_the_company", "why": "x"},
            )
        assert told["status"] == "not_proposed"
        record.assert_not_awaited()


@pytest.mark.asyncio
class TestConfirmingAndUndoing:
    async def test_confirm_arms_with_a_delay_rather_than_running(self):
        written: dict = {}

        async def write(event_id, *, organization_id, payload):
            written.update(payload)
            return True

        with (
            patch(
                "api.services.workflow.actions.db_client.get_agent_event",
                new=AsyncMock(return_value=_proposal(_switch(actions.PROPOSED))),
            ),
            patch(
                "api.services.workflow.actions.db_client.set_agent_event_payload",
                new=AsyncMock(side_effect=write),
            ),
            patch(
                "api.services.workflow.actions.db_client.set_workflow_live",
                new=AsyncMock(),
            ) as switch,
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            payload = await actions.settle(
                organization_id=7, event_id=99, verb="confirm", user_id=42
            )
        assert payload["state"] == actions.ARMED
        assert payload["confirmed"]["by"] == 42
        assert datetime.fromisoformat(payload["fires_at"]) > datetime.now(UTC)
        switch.assert_not_awaited()
        assert enqueue.await_args.args[:3] == ("run_proposed_action", 99, 7)
        assert enqueue.await_args.kwargs["_defer_by"].total_seconds() == (
            actions.UNDO_WINDOW_SECONDS
        )

    async def test_undo_inside_the_window_cancels_and_the_job_does_nothing(self):
        with (
            patch(
                "api.services.workflow.actions.db_client.get_agent_event",
                new=AsyncMock(return_value=_proposal(_switch(actions.ARMED))),
            ),
            patch(
                "api.services.workflow.actions.db_client.set_agent_event_payload",
                new=AsyncMock(return_value=True),
            ),
        ):
            payload = await actions.settle(
                organization_id=7, event_id=99, verb="undo", user_id=42
            )
        assert payload["state"] == actions.CANCELLED

        with (
            patch(
                "api.services.workflow.actions.db_client.get_agent_event",
                new=AsyncMock(return_value=_proposal(_switch(actions.CANCELLED))),
            ),
            patch(
                "api.services.workflow.actions.db_client.set_workflow_live",
                new=AsyncMock(),
            ) as switch,
        ):
            await actions.run(99, 7)
        switch.assert_not_awaited()

    async def test_the_job_runs_an_armed_switch_and_says_so_under_the_card(self):
        with (
            patch(
                "api.services.workflow.actions.db_client.get_agent_event",
                new=AsyncMock(return_value=_proposal(_switch(actions.ARMED))),
            ),
            patch(
                "api.services.workflow.actions.db_client.set_agent_event_payload",
                new=AsyncMock(return_value=True),
            ) as write,
            patch(
                "api.services.workflow.actions.db_client.set_workflow_live",
                new=AsyncMock(),
            ) as switch,
            patch(
                "api.services.workflow.actions.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            await actions.run(99, 7)
        assert switch.await_args.kwargs == {
            "workflow_id": 3,
            "is_live": True,
            "organization_id": 7,
        }
        assert write.await_args.kwargs["payload"]["state"] == actions.DONE
        line = record.await_args.kwargs
        assert line["kind"] == AgentEventKind.MESSAGE.value
        assert line["summary"] == "Front desk is now on."
        assert line["workflow_id"] == 3 and line["folder_id"] == 5

    async def test_a_done_switch_can_be_put_back(self):
        with (
            patch(
                "api.services.workflow.actions.db_client.get_agent_event",
                new=AsyncMock(return_value=_proposal(_switch(actions.DONE))),
            ),
            patch(
                "api.services.workflow.actions.db_client.set_agent_event_payload",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "api.services.workflow.actions.db_client.set_workflow_live",
                new=AsyncMock(),
            ) as switch,
            patch(
                "api.services.workflow.actions.agent_timeline.record", new=AsyncMock()
            ),
        ):
            payload = await actions.settle(
                organization_id=7, event_id=99, verb="undo", user_id=42
            )
        assert payload["state"] == actions.UNDONE
        assert switch.await_args.kwargs["is_live"] is False

    async def test_a_placed_call_cannot_be_put_back(self):
        with patch(
            "api.services.workflow.actions.db_client.get_agent_event",
            new=AsyncMock(return_value=_proposal(_callback(actions.DONE))),
        ):
            with pytest.raises(actions.ActionError, match="cannot be put back"):
                await actions.settle(
                    organization_id=7, event_id=99, verb="undo", user_id=42
                )

    async def test_a_refused_callback_is_a_state_on_the_card_not_a_crash(self):
        from api.services.telephony import missed_call

        with (
            patch(
                "api.services.workflow.actions.db_client.get_agent_event",
                new=AsyncMock(
                    return_value=_proposal(
                        _callback(actions.ARMED), workflow_id=None, folder_id=None
                    )
                ),
            ),
            patch(
                "api.services.workflow.actions.db_client.get_missed_call",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        id=12, caller="+919876543210", outcome="pending"
                    )
                ),
            ),
            patch(
                "api.services.telephony.missed_call.place_callback",
                new=AsyncMock(
                    side_effect=missed_call.CallbackRefused("Outside calling hours")
                ),
            ),
            patch(
                "api.services.workflow.actions.db_client.resolve_missed_call",
                new=AsyncMock(),
            ) as resolve,
            patch(
                "api.services.workflow.actions.db_client.set_agent_event_payload",
                new=AsyncMock(return_value=True),
            ) as write,
            patch(
                "api.services.workflow.actions.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            await actions.run(99, 7)
        assert resolve.await_args.kwargs["outcome"] == "refused"
        assert write.await_args.kwargs["payload"]["state"] == actions.FAILED
        assert write.await_args.kwargs["payload"]["error"] == "Outside calling hours"
        # On Decibyl's thread the line is Decibyl's, with no bot and no channel.
        line = record.await_args.kwargs
        assert line["payload"]["from"] == "Decibyl"
        assert line["workflow_id"] is None and line["in_channel"] is False

    async def test_a_second_confirm_is_refused(self):
        with patch(
            "api.services.workflow.actions.db_client.get_agent_event",
            new=AsyncMock(return_value=_proposal(_switch(actions.ARMED))),
        ):
            with pytest.raises(actions.ActionError, match="Already settled"):
                await actions.settle(
                    organization_id=7, event_id=99, verb="confirm", user_id=42
                )

    async def test_the_route_says_why_it_refused(self):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        try:
            with patch(
                "api.services.workflow.actions.db_client.get_agent_event",
                new=AsyncMock(return_value=_proposal(_switch(actions.DECLINED))),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/timeline/actions/settle",
                        json={"event_id": 99, "verb": "confirm"},
                    )
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert response.status_code == 409
        assert response.json()["detail"] == "Already settled."


@pytest.mark.asyncio
class TestDecibylProposes:
    async def test_decibyl_runs_the_tool_once_and_then_answers(self):
        from api.services.agent_builder.client import ModelReply, ToolCall
        from api.services.workflow import decibyl

        first = ModelReply(
            text="",
            tool_calls=(
                ToolCall(
                    id="c1",
                    name=actions.TOOL_NAME,
                    arguments={
                        "action": "turn_bot_on",
                        "bot": "Front desk",
                        "why": "x",
                    },
                ),
            ),
        )
        second = ModelReply(
            text="I have proposed turning Front desk on; confirm on the card."
        )
        complete = AsyncMock(side_effect=[first, second])
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
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        provider="openai", model="m", api_key="k"
                    )
                ),
            ),
            patch("api.services.agent_builder.client.complete", new=complete),
            patch(
                "api.services.workflow.actions.propose",
                new=AsyncMock(return_value={"status": "proposed", "note": "Proposed."}),
            ) as propose,
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ),
        ):
            body = await decibyl.answer(7, "turn front desk on")
        assert body.startswith("I have proposed")
        # The first call offers the tool, the second does not: one round.
        assert (
            complete.await_args_list[0].kwargs["tools"][0]["name"] == actions.TOOL_NAME
        )
        assert complete.await_args_list[1].kwargs["tools"] == []
        kwargs = propose.await_args.kwargs
        assert kwargs["workflow_id"] is None and kwargs["in_channel"] is False
        assert kwargs["arguments"]["bot"] == "Front desk"

    def test_decibyls_thread_carries_the_cards(self):
        from api.services.workflow import decibyl

        assert AgentEventKind.ACTION_PROPOSED.value in decibyl.thread_filter()["kinds"]

    def test_missed_calls_are_listed_with_the_id_the_tool_needs(self):
        from api.services.workflow import decibyl

        block = decibyl.missed_block(
            [
                SimpleNamespace(
                    id=12,
                    caller="+919876543210",
                    outcome="pending",
                    received_at=datetime(2026, 9, 14, 9, 30, tzinfo=UTC),
                )
            ]
        )
        assert "id 12: +919876543210 rang 14 Sep 09:30, pending" in block
        assert decibyl.missed_block([]) == "None in the last week."
