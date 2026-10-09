"""An edit card publishes or discards its own change, and nothing else.

Seen on staging in October 2026: the owner changed one step in the editor and
saved it, unpublished. The agent then proposed a change to another step from
the chat. Discard on that card deleted the owner's saved change too, because
``settle`` threw the whole draft away; Publish would have put it live
unreviewed, because ``settle`` published the whole draft. The card only ever
showed its own diff.

Now Publish applies exactly the card's change onto the live version and
leaves every other draft edit a draft; Discard takes the card's change out
of the draft and leaves the rest; and a card whose step was edited elsewhere
since is refused with a line on the card rather than guessed at.

DB integration tests: a real workflow, real draft versions, a real card row
written by ``self_edit.propose`` and a real audit table. Only the
acceptable-use model call is stubbed.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from api.db.models import AuditEntryModel, OrganizationModel, UserModel
from api.enums import AgentEventKind
from api.services.workflow import audit_log, publish_gate, self_edit

START = "Greet the caller and ask how you can help."
GRAPH = {
    "nodes": [
        {
            "id": "1",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Start",
                "prompt": START,
                "greeting_type": "text",
                "greeting": "Namaste, City Dental.",
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

#: What the owner typed into the End step in the editor and saved, unpublished.
OWNERS = "Thank them by name, then say goodbye."
#: What the agent proposed for the Start step from the chat.
PROPOSED = "Greet the caller warmly, ask their name, then how you can help."


def _prompt(definition: dict, node_id: str) -> str:
    for node in definition["nodes"]:
        if node["id"] == node_id:
            return node["data"].get("prompt", "")
    raise AssertionError(node_id)


def _greeting(definition: dict, node_id: str) -> str:
    for node in definition["nodes"]:
        if node["id"] == node_id:
            return node["data"].get("greeting", "")
    raise AssertionError(node_id)


def _screen(findings=()):
    return patch.object(
        publish_gate.acceptable_use, "screen", AsyncMock(return_value=list(findings))
    )


@pytest.fixture
async def bot(db_session, async_session):
    org = OrganizationModel(provider_id="test-org-edit-card-scope")
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id="test-user-edit-card-scope", selected_organization_id=org.id
    )
    async_session.add(user)
    await async_session.flush()
    workflow = await db_session.create_workflow(
        name="Front desk",
        workflow_definition=copy.deepcopy(GRAPH),
        user_id=user.id,
        organization_id=org.id,
    )

    async def editor_sets(node_id: str, prompt: str) -> None:
        """The owner edits one step in the editor and saves (no publish)."""
        draft = await db_session.get_draft_version(workflow.id)
        base = copy.deepcopy(
            draft.workflow_json
            if draft is not None
            else (
                await db_session.get_published_definition(workflow.id, org.id)
            ).workflow_json
        )
        for node in base["nodes"]:
            if node["id"] == node_id:
                node["data"]["prompt"] = prompt
        await db_session.save_workflow_draft(workflow.id, workflow_definition=base)

    async def propose(arguments: dict) -> int:
        """The agent proposes from the chat; returns the card's id."""
        result = await self_edit.propose(
            organization_id=org.id,
            workflow_id=workflow.id,
            workflow_run_id=None,
            arguments=arguments,
        )
        assert result["status"] == "proposed", result
        cards = list(
            await db_session.agent_events(
                organization_id=org.id,
                workflow_id=workflow.id,
                kinds=[AgentEventKind.EDIT_PROPOSED.value],
            )
        )
        return max(card.id for card in cards)

    async def settle(event_id: int, action: str) -> dict:
        return await self_edit.settle(
            organization_id=org.id,
            event_id=event_id,
            action=action,
            user_id=user.id,
        )

    async def live() -> dict:
        return (
            await db_session.get_published_definition(workflow.id, org.id)
        ).workflow_json

    async def draft() -> dict | None:
        row = await db_session.get_draft_version(workflow.id)
        return None if row is None else row.workflow_json

    async def card(event_id: int):
        return await db_session.get_agent_event(event_id, organization_id=org.id)

    async def published_audits() -> list[AuditEntryModel]:
        rows = await async_session.execute(
            select(AuditEntryModel).where(
                AuditEntryModel.organization_id == org.id,
                AuditEntryModel.action == audit_log.AGENT_PUBLISHED,
            )
        )
        return list(rows.scalars().all())

    return SimpleNamespace(
        org=org,
        user=user,
        workflow=workflow,
        editor_sets=editor_sets,
        propose=propose,
        settle=settle,
        live=live,
        draft=draft,
        card=card,
        published_audits=published_audits,
    )


@pytest.mark.asyncio
class TestTheStagingReport:
    async def test_discard_keeps_the_owners_unrelated_editor_change(self, bot):
        await bot.editor_sets("2", OWNERS)
        event_id = await bot.propose({"step": "Start", "new_prompt": PROPOSED})

        payload = await bot.settle(event_id, "discard")

        assert payload["decided"]["action"] == "discard"
        draft = await bot.draft()
        assert draft is not None, "the owner's saved change was thrown away"
        assert _prompt(draft, "2") == OWNERS
        assert _prompt(draft, "1") == START  # the card's change is gone
        assert _prompt(await bot.live(), "2") == "Bye"

    async def test_publish_leaves_the_owners_unrelated_change_unpublished(self, bot):
        await bot.editor_sets("2", OWNERS)
        event_id = await bot.propose({"step": "Start", "new_prompt": PROPOSED})

        with _screen() as screen:
            payload = await bot.settle(event_id, "publish")

        assert payload["decided"]["action"] == "publish"
        live = await bot.live()
        assert _prompt(live, "1") == PROPOSED  # the card's change is live
        assert _prompt(live, "2") == "Bye"  # the owner's is not
        draft = await bot.draft()
        assert draft is not None and _prompt(draft, "2") == OWNERS
        # The screen read what went live, not what is waiting.
        screened = screen.await_args.kwargs["instructions"]
        assert PROPOSED in screened and OWNERS not in screened
        audits = await bot.published_audits()
        assert [a.after["via"] for a in audits] == ["edit_card"]


@pytest.mark.asyncio
class TestPublish:
    async def test_the_cards_own_change_goes_live_and_the_draft_is_done(self, bot):
        event_id = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        with _screen():
            await bot.settle(event_id, "publish")
        assert _prompt(await bot.live(), "1") == PROPOSED
        # Nothing else was waiting, so nothing is left waiting.
        assert await bot.draft() is None

    async def test_a_find_and_replace_card_changes_only_the_name(self, bot):
        await bot.editor_sets("2", OWNERS)
        event_id = await bot.propose(
            {"find": "City Dental", "replace_with": "Sharma Dental"}
        )
        with _screen():
            await bot.settle(event_id, "publish")
        live = await bot.live()
        assert _greeting(live, "1") == "Namaste, Sharma Dental."
        assert _prompt(live, "2") == "Bye"
        assert _prompt(await bot.draft(), "2") == OWNERS

    async def test_a_greeting_card_publishes_the_greeting(self, bot):
        event_id = await bot.propose(
            {"step": "Start", "new_greeting": "Hello, Sharma Dental here."}
        )
        with _screen():
            await bot.settle(event_id, "publish")
        assert _greeting(await bot.live(), "1") == "Hello, Sharma Dental here."

    async def test_an_invalid_change_is_refused_by_the_gate(self, bot):
        event_id = await bot.propose(
            {"step": "Start", "new_prompt": "Welcome to {{clinic_name}}."}
        )
        with _screen():
            with pytest.raises(self_edit.EditError, match="cannot go live"):
                await bot.settle(event_id, "publish")
        assert (await bot.card(event_id)).payload["refused"]["kind"] == "invalid"
        assert _prompt(await bot.live(), "1") == START


@pytest.mark.asyncio
class TestConflicts:
    async def test_a_step_edited_since_is_refused_on_the_card(self, bot):
        event_id = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await bot.editor_sets("1", "The owner's own rewrite of the start.")

        with (
            _screen() as screen,
            patch.object(self_edit.agent_timeline, "record", AsyncMock()),
        ):
            with pytest.raises(self_edit.EditError, match="edited elsewhere since"):
                await bot.settle(event_id, "publish")

        screen.assert_not_awaited()
        assert _prompt(await bot.live(), "1") == START
        card = (await bot.card(event_id)).payload
        assert not card.get("decided")
        assert card["refused"]["kind"] == "conflict"
        assert "open the editor" in card["refused"]["reasons"][0]
        assert not await bot.published_audits()

    async def test_discard_after_the_step_moved_on_leaves_it_alone(self, bot):
        event_id = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await bot.editor_sets("1", "The owner's own rewrite of the start.")

        payload = await bot.settle(event_id, "discard")

        assert payload["decided"]["action"] == "discard"
        assert _prompt(await bot.draft(), "1") == (
            "The owner's own rewrite of the start."
        )

    async def test_a_card_whose_draft_was_thrown_away_is_stale(self, db_session, bot):
        event_id = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await db_session.discard_workflow_draft(bot.workflow.id)
        await bot.editor_sets("2", OWNERS)  # a new, unrelated draft

        with _screen(), patch.object(self_edit.agent_timeline, "record", AsyncMock()):
            with pytest.raises(self_edit.EditError, match="edited elsewhere since"):
                await bot.settle(event_id, "publish")
        assert _prompt(await bot.live(), "1") == START
        assert _prompt(await bot.live(), "2") == "Bye"

    async def test_a_card_from_before_this_fix_does_not_apply(self, db_session, bot):
        """No record of which nodes it changed: nothing to apply precisely."""
        await bot.editor_sets("2", OWNERS)
        event_id = await db_session.record_agent_event(
            organization_id=bot.org.id,
            kind=AgentEventKind.EDIT_PROPOSED.value,
            actor="agent",
            summary="Proposed a change to 2 steps",
            workflow_id=bot.workflow.id,
            payload={
                "workflow_id": bot.workflow.id,
                "step": "2 steps",
                "find": "Bye",
                "replace_with": "Goodbye",
            },
        )
        with _screen(), patch.object(self_edit.agent_timeline, "record", AsyncMock()):
            with pytest.raises(self_edit.EditError, match="open the editor"):
                await bot.settle(event_id, "publish")
        assert _prompt(await bot.live(), "2") == "Bye"
        # Discard on it leaves the draft as the owner left it.
        await bot.settle(event_id, "discard")
        assert _prompt(await bot.draft(), "2") == OWNERS

    async def test_another_accounts_card_cannot_discard_this_draft(
        self, db_session, async_session, bot
    ):
        await bot.editor_sets("2", OWNERS)
        other = OrganizationModel(provider_id="test-org-edit-card-scope-other")
        async_session.add(other)
        await async_session.flush()
        event_id = await db_session.record_agent_event(
            organization_id=other.id,
            kind=AgentEventKind.EDIT_PROPOSED.value,
            actor="agent",
            summary="Proposed a change",
            payload={"workflow_id": bot.workflow.id, "step": "End"},
        )
        with pytest.raises(self_edit.EditError, match="not found"):
            await self_edit.settle(
                organization_id=other.id,
                event_id=event_id,
                action="discard",
                user_id=bot.user.id,
            )
        assert _prompt(await bot.draft(), "2") == OWNERS
