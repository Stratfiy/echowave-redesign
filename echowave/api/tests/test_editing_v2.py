"""Agent editing follow-ups (editing_v2), against a real database.

1. One draft per change: every card is its own draft, so several wait side
   by side, publish or discard in any order, and neither they nor the
   editor's saved work write over each other.
2. Opening hours by chat: ``hours`` changes the agent's ``agent_schedule``
   -- the field the inbound route and the booking tools already keep -- and
   holiday closures shut the agent on that day.
3. Files by chat: ``attach_files`` / ``detach_files`` change the steps'
   ``document_uuids``, resolved among the workspace's own files only.
4. Undo: a published card can be taken back, exactly, through the same gate
   and audit; refused when its fields were edited since. "Undo that" by chat
   proposes the undo as a card.
5. Who may publish (default pending founder decision 11): the agent's owner
   and workspace admins; other members propose and wait.

And with the flag off, everything behaves as before.

Only the acceptable-use model call is stubbed.
"""

from __future__ import annotations

import copy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api import constants
from api.db.models import (
    AuditEntryModel,
    KnowledgeBaseDocumentModel,
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
)
from api.enums import AgentEventKind
from api.services.workflow import (
    agent_hours,
    audit_log,
    edit_permissions,
    publish_gate,
    self_edit,
)

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
PROPOSED = "Greet the caller warmly, ask their name, then how you can help."
IST = ZoneInfo("Asia/Kolkata")


def _node(definition: dict, node_id: str) -> dict:
    for node in definition["nodes"]:
        if node["id"] == node_id:
            return node["data"]
    raise AssertionError(node_id)


@pytest.fixture
def v2_on(monkeypatch):
    monkeypatch.setattr(constants, "EDITING_V2_ENABLED", True)


@pytest.fixture(autouse=True)
def _screen():
    with patch.object(
        publish_gate.acceptable_use, "screen", AsyncMock(return_value=[])
    ):
        yield


async def _person(async_session, org, tag: str, role: str) -> UserModel:
    user = UserModel(
        provider_id=f"test-editing-v2-{tag}", selected_organization_id=org.id
    )
    async_session.add(user)
    await async_session.flush()
    async_session.add(
        OrganizationMembershipModel(user_id=user.id, organization_id=org.id, role=role)
    )
    await async_session.flush()
    return user


@pytest.fixture
async def bot(db_session, async_session):
    org = OrganizationModel(provider_id="test-org-editing-v2")
    other_org = OrganizationModel(provider_id="test-org-editing-v2-other")
    async_session.add_all([org, other_org])
    await async_session.flush()
    owner = await _person(async_session, org, "owner", "member")
    member = await _person(async_session, org, "member", "member")
    admin = await _person(async_session, org, "admin", "admin")
    workflow = await db_session.create_workflow(
        name="Front desk",
        workflow_definition=copy.deepcopy(GRAPH),
        user_id=owner.id,
        organization_id=org.id,
    )

    async def propose(arguments: dict, *, expect="proposed") -> int | dict:
        result = await self_edit.propose(
            organization_id=org.id,
            workflow_id=workflow.id,
            workflow_run_id=None,
            arguments=arguments,
        )
        if expect != "proposed":
            assert result["status"] == expect, result
            return result
        assert result["status"] == "proposed", result
        cards = await db_session.agent_events(
            organization_id=org.id,
            workflow_id=workflow.id,
            kinds=[AgentEventKind.EDIT_PROPOSED.value],
        )
        return max(card.id for card in cards)

    async def settle(event_id: int, action: str, user=None) -> dict:
        return await self_edit.settle(
            organization_id=org.id,
            event_id=event_id,
            action=action,
            user_id=(user or owner).id,
        )

    async def live():
        return await db_session.get_published_definition(workflow.id, org.id)

    async def draft():
        return await db_session.get_draft_version(workflow.id)

    async def card(event_id: int) -> dict:
        row = await db_session.get_agent_event(event_id, organization_id=org.id)
        return dict(row.payload or {})

    async def editor_saves(node_id: str, prompt: str) -> None:
        """The owner edits one step in the editor, from the version on
        screen, and saves without publishing."""
        row = await db_session.get_draft_version(workflow.id)
        base = copy.deepcopy(
            row.workflow_json if row is not None else (await live()).workflow_json
        )
        _node(base, node_id)["prompt"] = prompt
        await db_session.save_workflow_draft(workflow.id, workflow_definition=base)

    async def editor_publishes(node_id: str, prompt: str) -> None:
        await editor_saves(node_id, prompt)
        await publish_gate.publish_draft(
            workflow_id=workflow.id, organization_id=org.id, user_id=owner.id
        )

    async def document(name: str, *, organization=None, scope="library"):
        row = KnowledgeBaseDocumentModel(
            organization_id=(organization or org).id,
            created_by=owner.id,
            filename=name,
            processing_status="completed",
            is_active=True,
            scope=scope,
        )
        async_session.add(row)
        await async_session.flush()
        return row

    async def audits() -> list[AuditEntryModel]:
        rows = await async_session.execute(
            select(AuditEntryModel).where(
                AuditEntryModel.organization_id == org.id,
                AuditEntryModel.action == audit_log.AGENT_PUBLISHED,
            )
        )
        return list(rows.scalars().all())

    return SimpleNamespace(
        org=org,
        other_org=other_org,
        owner=owner,
        member=member,
        admin=admin,
        workflow=workflow,
        propose=propose,
        settle=settle,
        live=live,
        draft=draft,
        card=card,
        editor_saves=editor_saves,
        editor_publishes=editor_publishes,
        document=document,
        audits=audits,
    )


# --- 1. one draft per change -------------------------------------------------


@pytest.mark.asyncio
class TestOneDraftPerChange:
    async def test_a_card_is_its_own_draft(self, bot, v2_on):
        event_id = await bot.propose({"step": "Start", "new_prompt": PROPOSED})

        assert (await bot.card(event_id))[self_edit.OWN_DRAFT] is True
        assert await bot.draft() is None, "the card wrote the shared draft"
        assert _node((await bot.live()).workflow_json, "1")["prompt"] == START

    async def test_cards_publish_in_any_order(self, bot, v2_on):
        prompt_card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        greeting_card = await bot.propose(
            {"step": "Start", "new_greeting": "Namaste, Smile Dental."}
        )
        end_card = await bot.propose({"step": "End", "new_prompt": "Thank them."})

        # Newest first, then the oldest, then the middle one.
        await bot.settle(end_card, "publish")
        await bot.settle(prompt_card, "publish")
        await bot.settle(greeting_card, "publish")

        live = (await bot.live()).workflow_json
        assert _node(live, "1")["prompt"] == PROPOSED
        assert _node(live, "1")["greeting"] == "Namaste, Smile Dental."
        assert _node(live, "2")["prompt"] == "Thank them."
        assert len(await bot.audits()) == 3

    async def test_discarding_one_leaves_the_others(self, bot, v2_on):
        first = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        second = await bot.propose({"step": "End", "new_prompt": "Thank them."})

        await bot.settle(first, "discard")
        await bot.settle(second, "publish")

        live = (await bot.live()).workflow_json
        assert _node(live, "1")["prompt"] == START
        assert _node(live, "2")["prompt"] == "Thank them."
        assert (await bot.card(first))["decided"]["action"] == "discard"

    async def test_an_editor_save_does_not_clobber_a_waiting_card(self, bot, v2_on):
        event_id = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        # The editor was open on the live version and saves a different step.
        await bot.editor_saves("2", "Thank them by name.")

        await bot.settle(event_id, "publish")

        live = (await bot.live()).workflow_json
        assert _node(live, "1")["prompt"] == PROPOSED
        assert _node(live, "2")["prompt"] == "Bye", "the editor's draft went live"
        draft = (await bot.draft()).workflow_json
        assert _node(draft, "2")["prompt"] == "Thank them by name."
        # Carried into the draft, so the editor's next Publish keeps it.
        assert _node(draft, "1")["prompt"] == PROPOSED

    async def test_a_card_does_not_clobber_the_editors_own_edit_of_that_field(
        self, bot, v2_on
    ):
        event_id = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await bot.editor_saves("1", "The owner's own words.")

        await bot.settle(event_id, "publish")

        draft = (await bot.draft()).workflow_json
        assert _node(draft, "1")["prompt"] == "The owner's own words."

    async def test_two_cards_on_one_field_the_second_is_refused(self, bot, v2_on):
        first = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        second = await bot.propose({"step": "Start", "new_prompt": "Something else."})
        await bot.settle(first, "publish")

        with pytest.raises(self_edit.EditError, match="edited elsewhere since"):
            await bot.settle(second, "publish")
        assert (await bot.card(second))["refused"]["kind"] == "conflict"

    async def test_find_and_replace_is_its_own_draft_too(self, bot, v2_on):
        event_id = await bot.propose({"find": "City Dental", "replace_with": "Smile"})
        assert await bot.draft() is None
        await bot.settle(event_id, "publish")
        assert _node((await bot.live()).workflow_json, "1")["greeting"] == (
            "Namaste, Smile."
        )


# --- 2. opening hours by chat ------------------------------------------------


WEEK = [{"days": ["mon-sat"], "open": "09:30", "close": "13:00"}]


@pytest.mark.asyncio
class TestOpeningHoursByChat:
    async def test_hours_become_a_card_with_a_readable_diff(self, bot, v2_on):
        event_id = await bot.propose(
            {
                "hours": {
                    "weekly": WEEK,
                    "closures": [{"date": "2026-11-08", "reason": "Diwali"}],
                },
                "why": "Clinic hours",
            }
        )
        payload = await bot.card(event_id)

        assert payload["what"] == "hours"
        assert payload["hours"]["before"] == ["No opening hours of its own"]
        assert payload["hours"]["after"][0] == "Monday to Saturday, 9:30 am to 1 pm."
        assert "Closed Sunday 8 November 2026 (Diwali)" in payload["hours"]["after"]
        assert payload["changes"][0]["config"] == agent_hours.CONFIG_KEY
        # Nothing is live until somebody publishes.
        live = await bot.live()
        assert not (live.workflow_configurations or {}).get(agent_hours.CONFIG_KEY)

    async def test_published_hours_are_the_ones_the_platform_keeps(self, bot, v2_on):
        event_id = await bot.propose(
            {
                "hours": {
                    "weekly": WEEK,
                    "closures": [{"date": "2026-11-09", "reason": "Diwali"}],
                }
            }
        )
        await bot.settle(event_id, "publish")

        schedule = (await bot.live()).workflow_configurations[agent_hours.CONFIG_KEY]
        assert schedule["enabled"] is True
        assert len(schedule["slots"]) == 6
        # A Monday in hours, and the same Monday shut for the holiday.
        assert agent_hours.is_open(schedule, datetime(2026, 11, 2, 10, 0, tzinfo=IST))
        assert not agent_hours.is_open(
            schedule, datetime(2026, 11, 9, 10, 0, tzinfo=IST)
        )
        line = agent_hours.hours_line(
            schedule, datetime(2026, 11, 9, 10, 0, tzinfo=IST)
        )
        assert "You are closed right now" in line
        assert "you open again tomorrow at 9:30 am" in line

    async def test_a_closure_can_be_reopened(self, bot, v2_on):
        card = await bot.propose(
            {"hours": {"weekly": WEEK, "closures": ["2026-11-09"]}}
        )
        await bot.settle(card, "publish")
        card = await bot.propose({"hours": {"reopen_dates": ["2026-11-09"]}})
        await bot.settle(card, "publish")
        schedule = (await bot.live()).workflow_configurations[agent_hours.CONFIG_KEY]
        assert schedule["closures"] == []
        assert len(schedule["slots"]) == 6

    async def test_unreadable_hours_are_refused_with_why(self, bot, v2_on):
        result = await bot.propose(
            {"hours": {"weekly": [{"days": ["funday"], "open": "9", "close": "5"}]}},
            expect="not_proposed",
        )
        assert "not a day of the week" in result["reason"]
        result = await bot.propose(
            {"hours": {"timezone": "Mars/Olympus"}}, expect="not_proposed"
        )
        assert "not a time zone" in result["reason"]

    async def test_the_same_hours_again_are_not_a_change(self, bot, v2_on):
        await bot.settle(await bot.propose({"hours": {"weekly": WEEK}}), "publish")
        result = await bot.propose({"hours": {"weekly": WEEK}}, expect="not_proposed")
        assert "already" in result["reason"]

    async def test_the_hours_card_leaves_the_rest_of_the_configuration(
        self, bot, v2_on
    ):
        before = dict((await bot.live()).workflow_configurations or {})
        await bot.settle(await bot.propose({"hours": {"weekly": WEEK}}), "publish")
        after = dict((await bot.live()).workflow_configurations)
        after.pop(agent_hours.CONFIG_KEY)
        before.pop(agent_hours.CONFIG_KEY, None)
        assert after == before


# --- 3. files by chat --------------------------------------------------------


@pytest.mark.asyncio
class TestFilesByChat:
    async def test_attach_then_detach(self, bot, v2_on):
        price = await bot.document("price-list-2026.pdf")

        event_id = await bot.propose({"attach_files": ["price list"]})
        payload = await bot.card(event_id)
        assert payload["files"]["added"] == [
            {"uuid": price.document_uuid, "name": "price-list-2026.pdf"}
        ]
        await bot.settle(event_id, "publish")
        live = (await bot.live()).workflow_json
        assert _node(live, "1")["document_uuids"] == [price.document_uuid]

        event_id = await bot.propose({"detach_files": ["price-list-2026.pdf"]})
        assert (await bot.card(event_id))["files"]["removed"][0]["uuid"] == (
            price.document_uuid
        )
        await bot.settle(event_id, "publish")
        assert _node((await bot.live()).workflow_json, "1")["document_uuids"] == []

    async def test_another_workspaces_file_is_not_found(self, bot, v2_on):
        await bot.document("old-menu.pdf", organization=bot.other_org)
        result = await bot.propose(
            {"attach_files": ["old-menu.pdf"]}, expect="not_proposed"
        )
        assert "no file called" in result["reason"]

    async def test_company_knowledge_cannot_be_detached_by_one_agent(self, bot, v2_on):
        await bot.document("handbook.pdf", scope="org")
        result = await bot.propose(
            {"detach_files": ["handbook.pdf"]}, expect="not_proposed"
        )
        assert "every agent in the workspace reads it" in result["reason"]

    async def test_an_ambiguous_name_asks_which(self, bot, v2_on):
        await bot.document("menu-lunch.pdf")
        await bot.document("menu-dinner.pdf")
        result = await bot.propose({"attach_files": ["menu"]}, expect="not_proposed")
        assert "Say which one" in result["reason"]


# --- 4. undo -----------------------------------------------------------------


@pytest.mark.asyncio
class TestUndo:
    async def test_undo_reverts_exactly_that_card(self, bot, v2_on):
        prompt_card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        end_card = await bot.propose({"step": "End", "new_prompt": "Thank them."})
        await bot.settle(prompt_card, "publish")
        await bot.settle(end_card, "publish")

        payload = await bot.settle(prompt_card, "undo")

        assert payload["undone"]["by"] == bot.owner.id
        live = (await bot.live()).workflow_json
        assert _node(live, "1")["prompt"] == START
        assert _node(live, "2")["prompt"] == "Thank them."
        vias = [a.after.get("via") for a in await bot.audits()]
        assert vias.count(publish_gate.VIA_EDIT_CARD_UNDO) == 1

    async def test_undo_of_hours_puts_the_old_hours_back(self, bot, v2_on):
        card = await bot.propose({"hours": {"weekly": WEEK}})
        await bot.settle(card, "publish")
        await bot.settle(card, "undo")
        configs = (await bot.live()).workflow_configurations or {}
        assert not configs.get(agent_hours.CONFIG_KEY)

    async def test_undo_is_refused_when_edited_elsewhere_since(self, bot, v2_on):
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await bot.settle(card, "publish")
        await bot.editor_publishes("1", "The owner's later words.")

        with pytest.raises(self_edit.EditError, match="edited elsewhere since"):
            await bot.settle(card, "undo")
        payload = await bot.card(card)
        assert payload["undo_refused"]["kind"] == "conflict"
        assert "undone" not in payload
        assert _node((await bot.live()).workflow_json, "1")["prompt"] == (
            "The owner's later words."
        )

    async def test_only_a_published_card_can_be_undone_and_only_once(self, bot, v2_on):
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        with pytest.raises(self_edit.EditError, match="Only a published change"):
            await bot.settle(card, "undo")
        await bot.settle(card, "publish")
        await bot.settle(card, "undo")
        with pytest.raises(self_edit.EditError, match="Already undone"):
            await bot.settle(card, "undo")

    async def test_undo_that_by_chat_is_a_card(self, bot, v2_on):
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await bot.settle(card, "publish")

        undo_card = await bot.propose({"undo": True, "why": "undo that"})
        payload = await bot.card(undo_card)
        assert payload["what"] == "undo"
        assert payload["undo_of"] == card
        assert "-" + PROPOSED in payload["diff"].replace("\n", "\n")
        # Nothing changes until it is published.
        assert _node((await bot.live()).workflow_json, "1")["prompt"] == PROPOSED

        await bot.settle(undo_card, "publish")
        assert _node((await bot.live()).workflow_json, "1")["prompt"] == START
        assert (await bot.card(card))["undone"]["by_card"] == undo_card

    async def test_undo_that_with_nothing_published(self, bot, v2_on):
        result = await bot.propose({"undo": True}, expect="not_proposed")
        assert "no published change" in result["reason"]


# --- 5. who may publish (default pending founder decision 11) ----------------


@pytest.mark.asyncio
class TestWhoMayPublish:
    async def test_a_member_proposes_and_waits(self, bot, v2_on):
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        with pytest.raises(self_edit.EditForbidden, match="to publish"):
            await bot.settle(card, "publish", user=bot.member)
        assert _node((await bot.live()).workflow_json, "1")["prompt"] == START

    async def test_the_owner_and_an_admin_may(self, bot, v2_on):
        first = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        second = await bot.propose({"step": "End", "new_prompt": "Thank them."})
        await bot.settle(first, "publish", user=bot.owner)
        await bot.settle(second, "publish", user=bot.admin)
        await bot.settle(first, "undo", user=bot.admin)

    async def test_a_member_cannot_undo(self, bot, v2_on):
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await bot.settle(card, "publish")
        with pytest.raises(self_edit.EditForbidden):
            await bot.settle(card, "undo", user=bot.member)

    async def test_a_member_may_still_discard(self, bot, v2_on):
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await bot.settle(card, "discard", user=bot.member)

    async def test_the_rule_is_one_constant(self):
        assert edit_permissions.PUBLISH_RULE.agent_owner is True
        assert edit_permissions.PUBLISH_RULE.min_role.value == "admin"

    async def test_the_card_says_who_to_wait_for(self, bot, v2_on):
        from api.routes.agent_timeline import edit_publisher

        allowed = await edit_publisher(workflow_id=bot.workflow.id, user=bot.owner)
        assert allowed.can_publish is True
        waiting = await edit_publisher(workflow_id=bot.workflow.id, user=bot.member)
        assert waiting.can_publish is False
        assert waiting.waiting.startswith("Waiting for ")
        assert waiting.waiting.endswith("or a workspace admin to publish.")

    async def test_another_workspaces_agent_is_not_found(self, bot, v2_on):
        from api.routes.agent_timeline import edit_publisher

        outsider = UserModel(
            provider_id="test-editing-v2-outsider",
            selected_organization_id=bot.other_org.id,
        )
        with pytest.raises(HTTPException) as caught:
            await edit_publisher(workflow_id=bot.workflow.id, user=outsider)
        assert caught.value.status_code == 404

    async def test_the_voice_row_follows_the_same_rule(self, bot, v2_on):
        from api.routes import workflow as workflow_routes

        request = workflow_routes.LiveVoiceRequest(
            component="tts", provider="x", model="y", voice="z"
        )
        with pytest.raises(HTTPException) as caught:
            await workflow_routes.set_voice_live(bot.workflow.id, request, bot.member)
        assert caught.value.status_code == 403

    async def test_the_voice_row_is_open_to_members_with_the_flag_off(self, bot):
        from api.routes import workflow as workflow_routes
        from api.services.workflow import live_voice

        request = workflow_routes.LiveVoiceRequest(
            component="tts", provider="x", model="y", voice="z"
        )
        reached = AsyncMock(side_effect=live_voice.AgentNotFound("stop here"))
        with patch.object(live_voice, "apply_voice_now", reached):
            with pytest.raises(HTTPException) as caught:
                await workflow_routes.set_voice_live(
                    bot.workflow.id, request, bot.member
                )
        assert caught.value.status_code == 404
        reached.assert_awaited_once()


# --- isolation and the flag off ---------------------------------------------


@pytest.mark.asyncio
class TestIsolation:
    async def test_a_card_cannot_be_settled_from_another_workspace(self, bot, v2_on):
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        with pytest.raises(self_edit.EditError, match="not here"):
            await self_edit.settle(
                organization_id=bot.other_org.id,
                event_id=card,
                action="publish",
                user_id=bot.owner.id,
            )

    async def test_undo_that_only_finds_this_agents_cards(self, bot, v2_on, db_session):
        other = await db_session.create_workflow(
            name="Back office",
            workflow_definition=copy.deepcopy(GRAPH),
            user_id=bot.owner.id,
            organization_id=bot.org.id,
        )
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await bot.settle(card, "publish")
        result = await self_edit.propose(
            organization_id=bot.org.id,
            workflow_id=other.id,
            workflow_run_id=None,
            arguments={"undo": True},
        )
        assert result["status"] == "not_proposed"


@pytest.mark.asyncio
class TestFlagOff:
    async def test_a_card_writes_the_shared_draft_as_before(self, bot):
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        assert self_edit.OWN_DRAFT not in await bot.card(card)
        assert _node((await bot.draft()).workflow_json, "1")["prompt"] == PROPOSED

    async def test_a_member_may_publish_as_before(self, bot):
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await bot.settle(card, "publish", user=bot.member)
        assert _node((await bot.live()).workflow_json, "1")["prompt"] == PROPOSED

    async def test_there_is_no_undo(self, bot):
        card = await bot.propose({"step": "Start", "new_prompt": PROPOSED})
        await bot.settle(card, "publish")
        with pytest.raises(self_edit.EditError, match="Publish or discard"):
            await bot.settle(card, "undo")

    async def test_hours_and_files_are_not_offered_or_taken(self, bot):
        properties = self_edit.tool_properties(bot.org.id)
        assert "hours" not in properties and "undo" not in properties
        assert self_edit.description(bot.org.id) == self_edit.DESCRIPTION
        result = await bot.propose({"hours": {"weekly": WEEK}}, expect="not_proposed")
        assert "step" in result["reason"]

    async def test_with_the_flag_on_they_are_offered(self, bot, v2_on):
        properties = self_edit.tool_properties(bot.org.id)
        assert {"hours", "attach_files", "detach_files", "undo"} <= set(properties)


class TestHoursRules:
    """The closure rules in agent_hours, which the call pipeline reads."""

    def _schedule(self, **extra):
        return {
            "enabled": True,
            "timezone": "Asia/Kolkata",
            "slots": [
                {"day_of_week": d, "start_time": "09:30", "end_time": "13:00"}
                for d in range(6)
            ],
            **extra,
        }

    def test_a_closure_shuts_the_whole_day(self):
        schedule = self._schedule(closures=[{"date": "2026-11-09"}])
        assert agent_hours.open_windows(schedule, datetime(2026, 11, 9).date()) == []
        assert agent_hours.open_windows(schedule, datetime(2026, 11, 10).date()) == [
            (570, 780)
        ]

    def test_an_unreadable_closure_closes_nothing(self):
        schedule = self._schedule(closures=[{"date": "9th November"}])
        assert agent_hours.is_open(schedule, datetime(2026, 11, 9, 10, tzinfo=IST))

    def test_a_disabled_schedule_ignores_closures(self):
        schedule = self._schedule(enabled=False, closures=[{"date": "2026-11-09"}])
        assert agent_hours.is_open(schedule, datetime(2026, 11, 9, 10, tzinfo=IST))

    def test_no_hours_no_line(self):
        assert agent_hours.hours_line(None) == ""
        assert agent_hours.hours_line({"enabled": False}) == ""


@pytest.mark.asyncio
class TestTheAgentKnowsItsHours:
    """Closed means the agent says when it is open: the line it is given
    with today's date, read from the hours just published."""

    def _engine(self, bot):
        from api.services.workflow.pipecat_engine import PipecatEngine

        async def organization_id():
            return bot.org.id

        async def workflow_id():
            return bot.workflow.id

        fake = SimpleNamespace(
            _get_organization_id=organization_id,
            _get_workflow_id=workflow_id,
            _workflow_run_id=None,
        )
        return lambda: PipecatEngine._get_hours_line(fake)

    async def test_published_hours_reach_the_agent(self, bot, v2_on):
        await bot.settle(await bot.propose({"hours": {"weekly": WEEK}}), "publish")
        line = await self._engine(bot)()
        assert line.startswith("Your opening hours: Monday to Saturday, 9:30 am")

    async def test_nothing_with_the_flag_off(self, bot, monkeypatch):
        monkeypatch.setattr(constants, "EDITING_V2_ENABLED", True)
        await bot.settle(await bot.propose({"hours": {"weekly": WEEK}}), "publish")
        monkeypatch.setattr(constants, "EDITING_V2_ENABLED", False)
        assert await self._engine(bot)() == ""
