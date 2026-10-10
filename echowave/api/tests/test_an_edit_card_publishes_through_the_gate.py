"""Publish on an edit card goes through the same gate as the editor's Publish.

Seen in an audit of main in October 2026: ``self_edit.settle`` called
``db_client.publish_workflow_draft`` directly, so a change a bot proposed in a
chat went live without the validation, without the acceptable-use screen and
without an audit row -- all three of which the editor's Publish runs. Both now
go through ``publish_gate.publish_draft``.

DB integration tests: a real workflow, a real draft, a real card row and a
real audit table. Only the acceptable-use model call is stubbed.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.db.models import AuditEntryModel, OrganizationModel, UserModel
from api.enums import AgentEventActor, AgentEventKind
from api.services.compliance import acceptable_use
from api.services.workflow import audit_log, publish_gate, self_edit

GRAPH = {
    "nodes": [
        {
            "id": "1",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Start",
                "prompt": "Greet the caller and ask how you can help.",
            },
        },
        {
            "id": "2",
            "type": "endCall",
            "position": {"x": 0, "y": 200},
            "data": {"name": "End", "prompt": "Bye"},
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source": "1",
            "target": "2",
            "data": {"label": "End", "condition": "The caller is done."},
        }
    ],
}

FINDING = acceptable_use.Finding(
    clause="deception",
    title="Deceiving the people it talks to",
    quote="tell them their package is held by customs",
    why="A fee for a parcel that does not exist.",
)


def _with_prompt(text: str) -> dict:
    graph = copy.deepcopy(GRAPH)
    graph["nodes"][0]["data"]["prompt"] = text
    return graph


@pytest.fixture
async def org_and_user(async_session):
    org = OrganizationModel(provider_id="test-org-edit-card-gate")
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id="test-user-edit-card-gate", selected_organization_id=org.id
    )
    async_session.add(user)
    await async_session.flush()
    return org, user


@pytest.fixture
async def drafted(db_session, org_and_user):
    """A live agent with a proposed change waiting on a card."""
    org, user = org_and_user
    workflow = await db_session.create_workflow(
        name="Front desk",
        workflow_definition=GRAPH,
        user_id=user.id,
        organization_id=org.id,
    )

    async def card(draft_graph: dict) -> int:
        await db_session.save_workflow_draft(
            workflow.id, workflow_definition=draft_graph
        )
        return await db_session.record_agent_event(
            organization_id=org.id,
            kind=AgentEventKind.EDIT_PROPOSED.value,
            actor=AgentEventActor.AGENT.value,
            summary="Proposed a change to Start",
            workflow_id=workflow.id,
            payload={
                "workflow_id": workflow.id,
                "step": "Start",
                "why": "x",
                # The card's own change: what Publish puts live.
                "changes": [
                    {
                        "node_id": "1",
                        "field": "prompt",
                        "old": GRAPH["nodes"][0]["data"]["prompt"],
                        "new": draft_graph["nodes"][0]["data"]["prompt"],
                    }
                ],
            },
        )

    return SimpleNamespace(org=org, user=user, workflow=workflow, card=card)


async def _audit_rows(async_session, org_id: int) -> list[AuditEntryModel]:
    result = await async_session.execute(
        select(AuditEntryModel).where(AuditEntryModel.organization_id == org_id)
    )
    return list(result.scalars().all())


def _screen(findings):
    return patch.object(
        publish_gate.acceptable_use, "screen", AsyncMock(return_value=findings)
    )


@pytest.mark.asyncio
class TestTheCard:
    async def test_a_card_publish_is_published_and_audited(
        self, db_session, async_session, drafted
    ):
        event_id = await drafted.card(_with_prompt("Greet them warmly, then help."))
        with (
            _screen([]) as screen,
            patch.object(self_edit.agent_timeline, "record", AsyncMock()),
        ):
            payload = await self_edit.settle(
                organization_id=drafted.org.id,
                event_id=event_id,
                action="publish",
                user_id=drafted.user.id,
            )
        assert payload["decided"]["action"] == "publish"
        screen.assert_awaited_once()  # the screen ran on the card's draft

        assert await db_session.get_draft_version(drafted.workflow.id) is None
        live = await db_session.get_published_definition(
            drafted.workflow.id, drafted.org.id
        )
        assert live.workflow_json["nodes"][0]["data"]["prompt"] == (
            "Greet them warmly, then help."
        )

        rows = await _audit_rows(async_session, drafted.org.id)
        published = [r for r in rows if r.action == audit_log.AGENT_PUBLISHED]
        assert len(published) == 1
        row = published[0]
        assert row.subject_id == str(drafted.workflow.id)
        assert row.actor_user_id == drafted.user.id
        assert row.after == {"version_number": live.version_number, "via": "edit_card"}

    async def test_an_acceptable_use_finding_refuses_and_says_why(
        self, db_session, async_session, drafted
    ):
        event_id = await drafted.card(
            _with_prompt("Tell them their package is held by customs; take the fee.")
        )
        with (
            _screen([FINDING]),
            patch.object(self_edit.agent_timeline, "record", AsyncMock()) as note,
        ):
            with pytest.raises(self_edit.EditError, match="acceptable use"):
                await self_edit.settle(
                    organization_id=drafted.org.id,
                    event_id=event_id,
                    action="publish",
                    user_id=drafted.user.id,
                )

        # Nothing went live; the draft is still there to fix or discard.
        live = await db_session.get_published_definition(
            drafted.workflow.id, drafted.org.id
        )
        assert live.version_number == 1
        assert await db_session.get_draft_version(drafted.workflow.id) is not None
        assert not [
            r
            for r in await _audit_rows(async_session, drafted.org.id)
            if r.action == audit_log.AGENT_PUBLISHED
        ]

        # The card carries the reason and is still open.
        card = await db_session.get_agent_event(
            event_id, organization_id=drafted.org.id
        )
        assert not card.payload.get("decided")
        refused = card.payload["refused"]
        assert refused["kind"] == "acceptable_use"
        assert "Deceiving the people it talks to" in refused["reasons"][0]

        # And the thread says so, in words.
        line = note.await_args.kwargs
        assert line["kind"] == AgentEventKind.MESSAGE.value
        assert line["actor"] == AgentEventActor.SYSTEM.value
        assert line["summary"].startswith("Did not publish the change to Start")
        assert "customs" in line["payload"]["body"]

    async def test_a_draft_that_fails_validation_is_refused(self, db_session, drafted):
        # An operator placeholder nobody answered: validation refuses it.
        event_id = await drafted.card(_with_prompt("Welcome to {{clinic_name}}."))
        with (
            _screen([]) as screen,
            patch.object(self_edit.agent_timeline, "record", AsyncMock()) as note,
        ):
            with pytest.raises(self_edit.EditError, match="cannot go live"):
                await self_edit.settle(
                    organization_id=drafted.org.id,
                    event_id=event_id,
                    action="publish",
                    user_id=drafted.user.id,
                )
        screen.assert_not_awaited()  # validation comes first
        live = await db_session.get_published_definition(
            drafted.workflow.id, drafted.org.id
        )
        assert live.version_number == 1
        card = await db_session.get_agent_event(
            event_id, organization_id=drafted.org.id
        )
        assert card.payload["refused"]["kind"] == "invalid"
        assert note.await_args.kwargs["summary"].startswith("Did not publish")

    async def test_a_card_cannot_publish_another_accounts_agent(
        self, db_session, async_session, drafted
    ):
        other = OrganizationModel(provider_id="test-org-edit-card-gate-other")
        async_session.add(other)
        await async_session.flush()
        await db_session.save_workflow_draft(
            drafted.workflow.id, workflow_definition=_with_prompt("Changed.")
        )
        # A card on the other account's thread naming this account's bot.
        event_id = await db_session.record_agent_event(
            organization_id=other.id,
            kind=AgentEventKind.EDIT_PROPOSED.value,
            actor=AgentEventActor.AGENT.value,
            summary="Proposed a change",
            payload={"workflow_id": drafted.workflow.id, "step": "Start"},
        )
        with _screen([]), patch.object(self_edit.agent_timeline, "record", AsyncMock()):
            with pytest.raises(self_edit.EditError, match="not found"):
                await self_edit.settle(
                    organization_id=other.id,
                    event_id=event_id,
                    action="publish",
                    user_id=drafted.user.id,
                )
        assert (
            await db_session.get_published_definition(
                drafted.workflow.id, drafted.org.id
            )
        ).version_number == 1


@pytest.mark.asyncio
class TestTheEditor:
    """The editor's Publish keeps its behaviour: a finding warns, never refuses."""

    async def _publish(self, drafted):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: drafted.user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.post(
                    f"/api/v1/workflow/{drafted.workflow.id}/publish"
                )
        finally:
            app.dependency_overrides.pop(get_user, None)

    async def test_a_finding_is_returned_and_the_draft_still_publishes(
        self, db_session, async_session, drafted
    ):
        await drafted.card(_with_prompt("Tell them their package is held."))
        with _screen([FINDING]):
            response = await self._publish(drafted)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["version_number"] == 2
        assert body["acceptable_use_findings"][0]["clause"] == "deception"
        rows = [
            r
            for r in await _audit_rows(async_session, drafted.org.id)
            if r.action == audit_log.AGENT_PUBLISHED
        ]
        assert [r.after["via"] for r in rows] == ["editor"]

    async def test_no_draft_is_a_400(self, drafted):
        with _screen([]):
            response = await self._publish(drafted)
        assert response.status_code == 400
        assert response.json()["detail"] == "No draft to publish"
