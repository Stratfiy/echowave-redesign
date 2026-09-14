"""A bot edits itself when its owner asks, and the owner approves the diff.

Arrival tests: the steps block names the steps; a proposal finds the step,
writes the draft and posts a card with a diff; nothing is proposed for a
step that does not exist or a change that is not one; a click publishes or
discards the draft and stamps the card; the tool is offered on staff chats
and nowhere else.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.enums import AgentEventKind
from api.services.workflow import self_edit
from api.services.workflow.pipecat_engine_context_composer import (
    compose_functions_for_node,
)

DEFINITION = {
    "nodes": [
        {
            "id": "n1",
            "type": "agentNode",
            "data": {"name": "Find a slot", "prompt": "Ask for a date."},
        },
        {
            "id": "g",
            "type": "globalNode",
            "data": {"name": "Global", "prompt": "Be polite."},
        },
        {
            "id": "n2",
            "type": "agentNode",
            "data": {"name": "Confirm", "prompt": "Read it back."},
        },
        {"id": "s", "type": "startCall", "data": {"name": "Start"}},
    ]
}


class TestTheStepsBlock:
    def test_lists_rules_first_then_the_steps_with_prompts(self):
        block = self_edit.steps_block(DEFINITION)
        assert (
            block.index("### Rules")
            < block.index("### Find a slot")
            < block.index("### Confirm")
        )
        assert "Ask for a date." in block
        assert "### Start" not in block  # no prompt, nothing to edit

    def test_nothing_for_no_steps(self):
        assert self_edit.steps_block({}) == ""
        assert self_edit.steps_block(None) == ""

    def test_finds_a_step_by_name_or_id_case_insensitively(self):
        assert self_edit.find_step(DEFINITION, "find a slot")["id"] == "n1"
        assert self_edit.find_step(DEFINITION, "rules")["id"] == "g"
        assert self_edit.find_step(DEFINITION, "N2")["id"] == "n2"
        assert self_edit.find_step(DEFINITION, "Book") is None


@pytest.mark.asyncio
class TestProposing:
    async def _propose(self, arguments, *, workflow_definition=DEFINITION):
        import copy

        workflow = SimpleNamespace(
            id=3, workflow_definition=copy.deepcopy(workflow_definition)
        )
        saved = AsyncMock(return_value=SimpleNamespace(version_number=4))
        with (
            patch(
                "api.services.workflow.self_edit.db_client.get_workflow_by_id",
                new=AsyncMock(return_value=workflow),
            ),
            patch(
                "api.services.workflow.self_edit.db_client.save_workflow_draft",
                new=saved,
            ),
            patch(
                "api.services.workflow.self_edit.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            result = await self_edit.propose(
                organization_id=7,
                workflow_id=3,
                workflow_run_id=11,
                arguments=arguments,
            )
        return result, saved, record

    async def test_the_change_is_drafted_and_the_card_carries_the_diff(self):
        result, saved, record = await self._propose(
            {
                "step": "Find a slot",
                "new_prompt": "Ask for the patient's name, then a date.",
                "why": "Name first",
            }
        )
        assert result["status"] == "proposed"
        definition = saved.await_args.kwargs["workflow_definition"]
        assert (
            definition["nodes"][0]["data"]["prompt"]
            == "Ask for the patient's name, then a date."
        )
        # Only that step moved.
        assert definition["nodes"][1]["data"]["prompt"] == "Be polite."
        kwargs = record.await_args.kwargs
        assert kwargs["kind"] == AgentEventKind.EDIT_PROPOSED.value
        assert kwargs["payload"]["step"] == "Find a slot"
        assert "-Ask for a date." in kwargs["payload"]["diff"]
        assert "+Ask for the patient's name, then a date." in kwargs["payload"]["diff"]
        assert kwargs["payload"]["draft_version"] == 4

    async def test_the_rules_are_a_step_too(self):
        result, saved, _ = await self._propose(
            {
                "step": "Rules",
                "new_prompt": "Be polite. Never quote a price.",
                "why": "",
            }
        )
        assert result["status"] == "proposed"
        assert saved.await_args.kwargs["workflow_definition"]["nodes"][1]["data"][
            "prompt"
        ].endswith("price.")

    async def test_an_unknown_step_is_refused_with_the_list(self):
        result, saved, record = await self._propose(
            {"step": "Booking", "new_prompt": "x", "why": ""}
        )
        assert result["status"] == "not_proposed"
        assert "Find a slot" in result["reason"]
        assert not saved.await_count and not record.await_count

    async def test_the_same_words_are_not_a_change(self):
        result, saved, _ = await self._propose(
            {"step": "Confirm", "new_prompt": "Read it back.", "why": ""}
        )
        assert result["status"] == "not_proposed"
        assert not saved.await_count


@pytest.mark.asyncio
class TestSettling:
    async def _settle(self, action, *, decided=None, publish=None, discard=None):
        event = SimpleNamespace(
            id=9,
            kind=AgentEventKind.EDIT_PROPOSED.value,
            workflow_id=3,
            payload={"step": "Find a slot", "decided": decided},
        )
        publish = publish or AsyncMock()
        discard = discard or AsyncMock()
        with (
            patch(
                "api.services.workflow.self_edit.db_client.get_agent_event",
                new=AsyncMock(return_value=event),
            ),
            patch(
                "api.services.workflow.self_edit.db_client.publish_workflow_draft",
                new=publish,
            ),
            patch(
                "api.services.workflow.self_edit.db_client.discard_workflow_draft",
                new=discard,
            ),
            patch(
                "api.services.workflow.self_edit.db_client.set_agent_event_payload",
                new=AsyncMock(return_value=True),
            ) as stamp,
            patch(
                "api.services.workflow.self_edit.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            payload = await self_edit.settle(
                organization_id=7, event_id=9, action=action, user_id=42
            )
        return payload, publish, discard, stamp, record

    async def test_publish_puts_the_draft_live_and_stamps_the_card(self):
        payload, publish, discard, stamp, record = await self._settle("publish")
        publish.assert_awaited_once_with(3)
        assert not discard.await_count
        assert (
            payload["decided"]["action"] == "publish" and payload["decided"]["by"] == 42
        )
        assert stamp.await_args.kwargs["payload"]["decided"]["action"] == "publish"
        assert (
            record.await_args.kwargs["summary"] == "Published the change to Find a slot"
        )

    async def test_discard_throws_the_draft_away(self):
        payload, publish, discard, _, _ = await self._settle("discard")
        discard.assert_awaited_once_with(3)
        assert not publish.await_count
        assert payload["decided"]["action"] == "discard"

    async def test_a_settled_card_stays_settled(self):
        with pytest.raises(self_edit.EditError):
            await self._settle("publish", decided={"action": "discard"})

    async def test_a_draft_gone_from_the_editor_is_said_so(self):
        with pytest.raises(self_edit.EditError, match="No draft"):
            await self._settle(
                "publish",
                publish=AsyncMock(
                    side_effect=ValueError("No draft exists for workflow 3")
                ),
            )


@pytest.mark.asyncio
class TestWhereItIsOffered:
    def _node(self):
        return SimpleNamespace(
            document_uuids=None,
            tool_uuids=None,
            mcp_tool_filters=None,
            out_edges=[],
            node_type="agent",
        )

    async def test_offered_on_a_staff_chat_only(self):
        offered = await compose_functions_for_node(
            node=self._node(), custom_tool_manager=None, can_edit_self=True
        )
        assert any(getattr(f, "name", None) == self_edit.TOOL_NAME for f in offered)
        withheld = await compose_functions_for_node(
            node=self._node(), custom_tool_manager=None
        )
        assert not any(
            getattr(f, "name", None) == self_edit.TOOL_NAME for f in withheld
        )

    async def test_a_channel_reply_marks_its_session_as_staff(self):
        from api.services.workflow import channel_reply

        ensured = AsyncMock(side_effect=RuntimeError("stop here"))
        with (
            patch(
                "api.services.workflow.channel_reply.db_client.get_workflow_by_id",
                new=AsyncMock(
                    return_value=SimpleNamespace(id=3, name="x", organization_id=7)
                ),
            ),
            patch(
                "api.services.workflow.channel_reply.db_client.create_workflow_run",
                new=AsyncMock(return_value=SimpleNamespace(id=11)),
            ),
            patch(
                "api.services.workflow.channel_reply.authorize_workflow_run_start",
                new=AsyncMock(
                    return_value=SimpleNamespace(has_quota=True, error_message=None)
                ),
            ),
            patch(
                "api.services.workflow.channel_reply.db_client.ensure_workflow_run_text_session",
                new=ensured,
            ),
            patch(
                "api.services.workflow.channel_reply.agent_timeline.record",
                new=AsyncMock(),
            ),
        ):
            try:
                await channel_reply.answer_in_channel(3, 5, "hi")
            except RuntimeError:
                pass
        assert ensured.await_args.kwargs["session_data"]["staff_chat"] is True


@pytest.mark.asyncio
class TestTheClickRoute:
    async def test_the_click_settles_and_returns_the_row(self):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        row = SimpleNamespace(
            id=9,
            at=datetime(2026, 9, 14, tzinfo=UTC),
            kind="edit_proposed",
            actor="agent",
            summary="Proposed",
            payload={"decided": {"action": "publish"}},
            is_deliverable=False,
            workflow_id=3,
            workflow_run_id=None,
            folder_id=None,
            visibility="always",
        )
        try:
            with (
                patch(
                    "api.routes.agent_timeline.self_edit.settle",
                    new=AsyncMock(return_value={}),
                ) as settle,
                patch(
                    "api.routes.agent_timeline.db_client.get_agent_event",
                    new=AsyncMock(return_value=row),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/timeline/edits/settle",
                        json={"event_id": 9, "action": "Publish"},
                    )
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert response.status_code == 200, response.text
        assert settle.await_args.kwargs["action"] == "publish"
        assert response.json()["payload"]["decided"]["action"] == "publish"
