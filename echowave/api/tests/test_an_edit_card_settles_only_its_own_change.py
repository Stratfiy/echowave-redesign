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

from api import constants
from api.db.models import AuditEntryModel, OrganizationModel, UserModel
from api.enums import AgentEventKind
from api.services.escalation import policy as escalation_policy
from api.services.huddle import tools as huddle_tools
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
            with pytest.raises(self_edit.EditError, match="made before an update"):
                await bot.settle(event_id, "publish")
        assert _prompt(await bot.live(), "2") == "Bye"
        refused = (await bot.card(event_id)).payload["refused"]
        assert refused["kind"] == "legacy"
        assert refused["reasons"] == [self_edit.LEGACY]
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


# --- cards from before ``changes`` (production has some) ---------------------


async def _legacy_card(db_session, bot, payload: dict) -> int:
    """A card row exactly as an earlier release wrote it: no ``changes``."""
    return await db_session.record_agent_event(
        organization_id=bot.org.id,
        kind=AgentEventKind.EDIT_PROPOSED.value,
        actor="agent",
        summary=f"Proposed a change to {payload.get('step')}",
        workflow_id=bot.workflow.id,
        payload={"workflow_id": bot.workflow.id, "bot_name": "Front desk", **payload},
    )


async def _draft_sets(db_session, bot, node_id: str, **fields) -> None:
    """What an earlier release's ``propose`` wrote into the shared draft."""
    draft = await db_session.get_draft_version(bot.workflow.id)
    base = copy.deepcopy(draft.workflow_json if draft is not None else await bot.live())
    for node in base["nodes"]:
        if node["id"] == node_id:
            node["data"].update(fields)
    await db_session.save_workflow_draft(bot.workflow.id, workflow_definition=base)


@pytest.mark.asyncio
class TestCardsFromBeforeThisFix:
    async def test_a_whole_step_card_from_september_publishes_only_its_step(
        self, db_session, bot
    ):
        """The payload ``propose`` wrote before greetings existed (#522)."""
        await bot.editor_sets("2", OWNERS)
        await _draft_sets(db_session, bot, "1", prompt=PROPOSED)
        event_id = await _legacy_card(
            db_session,
            bot,
            {
                "step": "Start",
                "node_id": "1",
                "why": "Ask their name",
                "old": START,
                "new": PROPOSED,
                "diff": self_edit.unified_diff(START, PROPOSED, name="Start"),
                "draft_version": 2,
            },
        )
        with _screen():
            await bot.settle(event_id, "publish")
        live = await bot.live()
        assert _prompt(live, "1") == PROPOSED
        assert _prompt(live, "2") == "Bye"  # the owner's change did not go live
        assert _prompt(await bot.draft(), "2") == OWNERS

    async def test_a_greeting_card_from_before_publishes_the_greeting(
        self, db_session, bot
    ):
        """The payload #576 wrote for ``new_greeting``."""
        await bot.editor_sets("2", OWNERS)
        await _draft_sets(db_session, bot, "1", greeting="Hello, Sharma Dental.")
        event_id = await _legacy_card(
            db_session,
            bot,
            {
                "step": "Start",
                "node_id": "1",
                "why": "",
                "old": "",
                "new": "",
                "diff": "",
                "greetings": [
                    {
                        "step": "Start",
                        "node_id": "1",
                        "old": "Namaste, City Dental.",
                        "new": "Hello, Sharma Dental.",
                    }
                ],
                "draft_version": 2,
            },
        )
        with _screen():
            await bot.settle(event_id, "publish")
        live = await bot.live()
        assert _greeting(live, "1") == "Hello, Sharma Dental."
        assert _prompt(live, "2") == "Bye"

    async def test_a_find_and_replace_card_is_rebuilt_while_live_still_matches(
        self, db_session, bot
    ):
        """The payload #576 wrote for find and replace: the steps' text joined
        into one, so the change is rebuilt from live and checked against it."""
        await bot.editor_sets("2", OWNERS)
        await _draft_sets(db_session, bot, "1", greeting="Namaste, Sharma Dental.")
        event_id = await _legacy_card(
            db_session,
            bot,
            {
                "step": "Start",
                "steps": ["Start"],
                "node_id": "1",
                "why": "New name",
                "find": "City Dental",
                "replace_with": "Sharma Dental",
                "old": "",
                "new": "",
                "diff": "",
                "greetings": [
                    {
                        "step": "Start",
                        "node_id": "1",
                        "old": "Namaste, City Dental.",
                        "new": "Namaste, Sharma Dental.",
                    }
                ],
                "draft_version": 2,
            },
        )
        with _screen():
            await bot.settle(event_id, "publish")
        live = await bot.live()
        assert _greeting(live, "1") == "Namaste, Sharma Dental."
        assert _prompt(live, "2") == "Bye"
        assert _prompt(await bot.draft(), "2") == OWNERS

    async def test_a_find_and_replace_card_from_september_is_rebuilt(
        self, db_session, bot
    ):
        """#522's find and replace: prompts only, no ``greetings`` key."""
        await _draft_sets(db_session, bot, "2", prompt="Goodbye")
        event_id = await _legacy_card(
            db_session,
            bot,
            {
                "step": "End",
                "steps": ["End"],
                "node_id": "2",
                "why": "",
                "find": "Bye",
                "replace_with": "Goodbye",
                "old": "Bye",
                "new": "Goodbye",
                "diff": self_edit.unified_diff("Bye", "Goodbye", name="End"),
                "draft_version": 2,
            },
        )
        with _screen():
            await bot.settle(event_id, "publish")
        assert _prompt(await bot.live(), "2") == "Goodbye"
        assert await bot.draft() is None

    async def test_one_whose_text_moved_on_says_so_and_publishes_nothing(
        self, db_session, bot
    ):
        """Live no longer reads as the card showed: not rebuilt, not guessed,
        and never the whole draft. The card says why, honestly."""
        await bot.editor_sets("2", OWNERS)
        event_id = await _legacy_card(
            db_session,
            bot,
            {
                "step": "Start",
                "steps": ["Start"],
                "node_id": "1",
                "find": "City Dental",
                "replace_with": "Sharma Dental",
                "old": "",
                "new": "",
                # What the card showed is not what live holds.
                "greetings": [
                    {
                        "step": "Start",
                        "node_id": "1",
                        "old": "Welcome to City Dental.",
                        "new": "Welcome to Sharma Dental.",
                    }
                ],
            },
        )
        with (
            _screen() as screen,
            patch.object(self_edit.agent_timeline, "record", AsyncMock()),
        ):
            with pytest.raises(self_edit.EditError) as raised:
                await bot.settle(event_id, "publish")
        assert str(raised.value) == self_edit.LEGACY
        assert "edited elsewhere" not in str(raised.value)
        screen.assert_not_awaited()
        assert not await bot.published_audits()
        live = await bot.live()
        assert _greeting(live, "1") == "Namaste, City Dental."
        assert _prompt(live, "2") == "Bye"  # the draft did not go live either
        card = (await bot.card(event_id)).payload
        assert card["refused"]["kind"] == "legacy"
        assert not card.get("decided")

        payload = await bot.settle(event_id, "discard")
        assert payload["decided"]["action"] == "discard"
        assert _prompt(await bot.draft(), "2") == OWNERS  # touched nothing


# --- the escalation policy, proposed by chat (#579) ---------------------------


@pytest.fixture
def escalation_on(monkeypatch):
    monkeypatch.setattr(constants, "ESCALATION_V2_ENABLED", True)


ESCALATION = {
    "refund_limit": 2000,
    "always_transfer_topics": ["emergency", "refund_over_limit"],
}


async def _live_policy(db_session, bot):
    live = await db_session.get_published_definition(bot.workflow.id, bot.org.id)
    return (live.workflow_configurations or {}).get(escalation_policy.CONFIG_KEY)


@pytest.mark.asyncio
class TestAnEscalationCard:
    async def test_it_records_its_change_and_leaves_the_draft_alone(
        self, db_session, bot, escalation_on
    ):
        await bot.editor_sets("2", OWNERS)
        draft_before = await db_session.get_draft_version(bot.workflow.id)

        event_id = await bot.propose({"why": "Refunds", "escalation": ESCALATION})

        [change] = (await bot.card(event_id)).payload["changes"]
        assert change["config"] == escalation_policy.CONFIG_KEY
        assert change["new"]["refund_limit"] == 2000
        draft = await db_session.get_draft_version(bot.workflow.id)
        assert (draft.workflow_configurations or {}) == (
            draft_before.workflow_configurations or {}
        )
        assert escalation_policy.CONFIG_KEY not in (draft.workflow_configurations or {})

    async def test_publish_puts_the_policy_live_and_nothing_else(
        self, db_session, bot, escalation_on
    ):
        await bot.editor_sets("2", OWNERS)
        event_id = await bot.propose({"why": "Refunds", "escalation": ESCALATION})

        payload = await bot.settle(event_id, "publish")

        assert payload["decided"]["action"] == "publish"
        assert (await _live_policy(db_session, bot))["refund_limit"] == 2000
        live = await bot.live()
        assert _prompt(live, "2") == "Bye"  # the owner's draft did not go live
        draft = await db_session.get_draft_version(bot.workflow.id)
        assert _prompt(draft.workflow_json, "2") == OWNERS
        # The waiting draft carries the policy too, so publishing it later
        # does not quietly put the old one back.
        assert (
            draft.workflow_configurations[escalation_policy.CONFIG_KEY]["refund_limit"]
            == 2000
        )
        audits = await bot.published_audits()
        assert [a.after["via"] for a in audits] == ["edit_card"]

    async def test_discard_touches_nothing_and_nothing_goes_live_later(
        self, db_session, bot, escalation_on
    ):
        await bot.editor_sets("2", OWNERS)
        event_id = await bot.propose({"why": "Refunds", "escalation": ESCALATION})

        payload = await bot.settle(event_id, "discard")

        assert payload["decided"]["action"] == "discard"
        assert await _live_policy(db_session, bot) is None
        # The owner publishes their unrelated draft: the discarded policy
        # is nowhere in it, so it does not ride along.
        with _screen():
            await publish_gate.publish_draft(
                workflow_id=bot.workflow.id,
                organization_id=bot.org.id,
                user_id=bot.user.id,
            )
        assert _prompt(await bot.live(), "2") == OWNERS
        assert await _live_policy(db_session, bot) is None

    async def test_an_unrelated_publish_does_not_take_a_waiting_card_live(
        self, db_session, bot, escalation_on
    ):
        """Confirmed in the verification DB before this fix: the card's
        policy sat in the shared draft and went live with the next publish."""
        event_id = await bot.propose({"why": "Refunds", "escalation": ESCALATION})
        await bot.editor_sets("2", OWNERS)
        with _screen():
            await publish_gate.publish_draft(
                workflow_id=bot.workflow.id,
                organization_id=bot.org.id,
                user_id=bot.user.id,
            )
        assert await _live_policy(db_session, bot) is None
        # And the card still publishes on its own afterwards.
        await bot.settle(event_id, "publish")
        assert (await _live_policy(db_session, bot))["refund_limit"] == 2000
        assert _prompt(await bot.live(), "2") == OWNERS

    async def test_a_policy_changed_live_since_is_a_conflict(
        self, db_session, bot, escalation_on
    ):
        event_id = await bot.propose({"why": "Refunds", "escalation": ESCALATION})
        # Somebody else puts a different policy live first.
        other = await bot.propose(
            {"why": "Attempts", "escalation": {"max_ai_attempts": 4}}
        )
        await bot.settle(other, "publish")

        with patch.object(self_edit.agent_timeline, "record", AsyncMock()):
            with pytest.raises(self_edit.EditError, match="edited elsewhere since"):
                await bot.settle(event_id, "publish")
        assert (await _live_policy(db_session, bot))["max_ai_attempts"] == 4
        assert (await bot.card(event_id)).payload["refused"]["kind"] == "conflict"

    async def test_one_from_before_this_fix_is_taken_out_of_the_draft_on_discard(
        self, db_session, bot, escalation_on
    ):
        """#579's card wrote the shared draft and recorded no ``changes``:
        Publish says so honestly; Discard takes its policy back out of the
        draft -- only while the draft holds exactly what it wrote -- and
        leaves the owner's edits."""
        await bot.editor_sets("2", OWNERS)
        proposed = escalation_policy.validate_changes(
            {**escalation_policy.default_policy().model_dump(mode="json"), **ESCALATION}
        ).model_dump(mode="json")
        draft = await db_session.get_draft_version(bot.workflow.id)
        await db_session.save_workflow_draft(
            bot.workflow.id,
            workflow_configurations={
                **(draft.workflow_configurations or {}),
                escalation_policy.CONFIG_KEY: proposed,
            },
        )
        event_id = await _legacy_card(
            db_session,
            bot,
            {
                "step": "Escalation",
                "node_id": None,
                "old": "Refund limit: none",
                "new": "Refund limit: Rs 2000",
                "diff": "",
                "greetings": [],
                "escalation": proposed,
                "draft_version": draft.version_number,
            },
        )
        with patch.object(self_edit.agent_timeline, "record", AsyncMock()):
            with pytest.raises(self_edit.EditError, match="made before an update"):
                await bot.settle(event_id, "publish")
        assert await _live_policy(db_session, bot) is None

        await bot.settle(event_id, "discard")
        draft = await db_session.get_draft_version(bot.workflow.id)
        assert escalation_policy.CONFIG_KEY not in (draft.workflow_configurations or {})
        assert _prompt(draft.workflow_json, "2") == OWNERS


# --- every kind of proposal publishes and discards on its own -----------------


async def _via_huddle(bot, arguments: dict) -> int:
    result, cards = await huddle_tools.propose_edit(
        organization_id=bot.org.id, workflow_id=bot.workflow.id, arguments=arguments
    )
    assert result["status"] == "proposed", result
    [card] = cards
    return card


#: Every proposal ``settle`` can be asked about: (name, arguments, how it is
#: proposed, what the live version reads as once it is published).
KINDS = [
    ("whole step", {"step": "Start", "new_prompt": PROPOSED}, "chat"),
    ("new greeting", {"step": "Start", "new_greeting": "Hello there."}, "chat"),
    (
        "step and greeting",
        {"step": "Start", "new_prompt": PROPOSED, "new_greeting": "Hello there."},
        "chat",
    ),
    ("find and replace", {"find": "City Dental", "replace_with": "Sharma"}, "chat"),
    ("escalation", {"escalation": ESCALATION}, "chat"),
    ("huddle whole step", {"step": "Start", "new_prompt": PROPOSED}, "huddle"),
    ("huddle escalation", {"escalation": ESCALATION}, "huddle"),
]


def _reads(live_json: dict, live_policy, name: str) -> bool:
    if "escalation" in name:
        return bool(live_policy) and live_policy["refund_limit"] == 2000
    if name == "find and replace":
        return _greeting(live_json, "1") == "Namaste, Sharma."
    if name == "new greeting":
        return _greeting(live_json, "1") == "Hello there."
    if name == "step and greeting":
        return (
            _prompt(live_json, "1") == PROPOSED
            and _greeting(live_json, "1") == "Hello there."
        )
    return _prompt(live_json, "1") == PROPOSED


def test_every_kind_settle_handles_is_enumerated_here():
    """A proposal kind added to ``propose`` without a row in KINDS is a kind
    nobody checked publishes on its own. ``propose`` dispatches on these."""
    import inspect

    source = inspect.getsource(self_edit.propose)
    for dispatched in ("_propose_escalation", "_propose_replace"):
        assert dispatched in source
    assert {"escalation", "find and replace", "whole step", "new greeting"} <= {
        name for name, _, _ in KINDS
    }
    assert self_edit.CONFIGS == (escalation_policy.CONFIG_KEY,)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments,via", KINDS, ids=[k[0] for k in KINDS])
class TestEveryKind:
    async def _propose(self, bot, arguments, via) -> int:
        if via == "huddle":
            return await _via_huddle(bot, arguments)
        return await bot.propose(arguments)

    async def test_it_records_changes_and_publishes_alone(
        self, db_session, bot, escalation_on, name, arguments, via
    ):
        await bot.editor_sets("2", OWNERS)
        event_id = await self._propose(bot, arguments, via)
        assert (await bot.card(event_id)).payload.get("changes"), name

        with _screen():
            payload = await bot.settle(event_id, "publish")

        assert payload["decided"]["action"] == "publish"
        assert not payload["decided"].get("already_live")
        live = await bot.live()
        assert _reads(live, await _live_policy(db_session, bot), name)
        assert _prompt(live, "2") == "Bye"  # nothing else went live
        assert _prompt(await bot.draft(), "2") == OWNERS

    async def test_it_discards_alone(
        self, db_session, bot, escalation_on, name, arguments, via
    ):
        await bot.editor_sets("2", OWNERS)
        event_id = await self._propose(bot, arguments, via)

        payload = await bot.settle(event_id, "discard")

        assert payload["decided"]["action"] == "discard"
        live = await bot.live()
        assert not _reads(live, await _live_policy(db_session, bot), name)
        draft = await db_session.get_draft_version(bot.workflow.id)
        assert _prompt(draft.workflow_json, "2") == OWNERS
        assert not _reads(
            draft.workflow_json,
            (draft.workflow_configurations or {}).get(escalation_policy.CONFIG_KEY),
            name,
        )
