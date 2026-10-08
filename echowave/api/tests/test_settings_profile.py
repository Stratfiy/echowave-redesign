"""Settings shell: the person's own settings (screens 17-19; handoff 24, 30).

Done when: Account, Personalization and Voice save on controls'
member_preferences with its revision (a stale save shows both versions);
onboarding answers are read in once and never overwrite a later choice; one
member's settings never change another's or the workspace's; the choices
reach the next turn as preferences that grant nothing; memory starts off
until chosen; a temporary conversation keeps nothing and is deleted on time;
and with the switch off everything is as it was.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from api import constants
from api.db import db_client
from api.db.models import AgentEventModel
from api.services import member_preferences
from api.services.knowledge_graph import teach
from api.services.organization_preferences import get_organization_preferences
from api.services.settings import profile, temporary
from api.services.shell import onboarding
from api.tests.support.settings_people import clean, client_as, make_people


@pytest.fixture
async def people(test_engine):
    found = await make_people("setprof")
    try:
        yield found
    finally:
        await clean(found)


@pytest.fixture
def shell_on(monkeypatch):
    monkeypatch.setattr(constants, "SETTINGS_SHELL_ENABLED", True)
    monkeypatch.setattr(constants, "MEMBER_PREFERENCES_ENABLED", True)


@pytest.fixture
def memory_on(monkeypatch):
    monkeypatch.setattr(constants, "MEMORY_MANAGER_ENABLED", True)


@pytest.mark.asyncio
class TestTheSwitch:
    async def test_off_the_routes_are_not_there(self, people):
        async with client_as(people.as_user(people.a)) as c:
            assert (await c.get("/api/v1/me/settings/profile")).status_code == 404
            assert (await c.get("/api/v1/me/settings/voices")).status_code == 404
            assert (
                await c.post("/api/v1/me/temporary-conversations")
            ).status_code == 404

    async def test_off_a_turn_is_told_nothing_new(self, people):
        await member_preferences.save(
            people.a.id, {"custom_instructions": "Call me boss"}, revision=0
        )
        assert await profile.block_for_turn(people.org, people.a.id, None) is None
        assert await temporary.reason_for_turn(people.org, people.a.id, None) is None

    async def test_on_without_member_preferences_it_says_so(self, people, monkeypatch):
        monkeypatch.setattr(constants, "SETTINGS_SHELL_ENABLED", True)
        async with client_as(people.as_user(people.a)) as c:
            answer = await c.get("/api/v1/me/settings/profile")
        assert answer.status_code == 503
        assert "not switched on" in answer.json()["detail"]


@pytest.mark.asyncio
class TestTheDoorsAnswersArrive:
    async def test_onboarding_answers_are_read_in_once(self, people, shell_on):
        await onboarding.save(
            people.a.id,
            language="hi",
            timezone="Asia/Kolkata",
            timezone_confirmed=True,
            preferred_name="Nithya",
            complete=True,
        )
        async with client_as(people.as_user(people.a)) as c:
            got = (await c.get("/api/v1/me/settings/profile")).json()
        assert got["language"] == "hi-IN"
        assert got["timezone"] == "Asia/Kolkata"
        assert got["preferred_name"] == "Nithya"
        assert got["onboarding_absorbed_at"]
        # The language list says which can be spoken, native name first.
        hindi = next(lang for lang in got["languages"] if lang["tag"] == "hi-IN")
        assert hindi["native"] == "हिन्दी" and hindi["voice"] is True
        assert any(
            lang["tag"] == "ur-IN" and not lang["voice"] for lang in got["languages"]
        )

    async def test_a_later_choice_is_never_overwritten(self, people, shell_on):
        await member_preferences.save(people.a.id, {"language": "ta-IN"}, revision=0)
        await onboarding.save(
            people.a.id, language="hi", timezone="Asia/Kolkata", timezone_confirmed=True
        )
        stored = await profile.absorb_onboarding(people.a.id)
        assert stored["language"] == "ta-IN"
        # The empty timezone was filled; the chosen language was not touched.
        assert stored["timezone"] == "Asia/Kolkata"

    async def test_a_cleared_field_is_not_refilled(self, people, shell_on):
        await onboarding.save(
            people.a.id, language="hi", timezone="Asia/Kolkata", timezone_confirmed=True
        )
        first = await profile.absorb_onboarding(people.a.id)
        cleared = await member_preferences.save(
            people.a.id, {"language": None}, revision=first["revision"]
        )
        again = await profile.absorb_onboarding(people.a.id)
        assert again["language"] is None and again["revision"] == cleared["revision"]

    async def test_saving_at_the_door_reaches_settings_at_once(
        self, people, shell_on, monkeypatch
    ):
        monkeypatch.setattr(constants, "FIRST_TASK_ONBOARDING_ENABLED", True)
        async with client_as(people.as_user(people.a)) as c:
            saved = await c.put(
                "/api/v1/shell/onboarding",
                json={
                    "language": "ta",
                    "timezone": "Asia/Kolkata",
                    "timezone_confirmed": True,
                    "complete": False,
                },
            )
        assert saved.status_code == 200, saved.text
        assert (await member_preferences.get(people.a.id))["language"] == "ta-IN"

    async def test_an_unconfirmed_timezone_is_never_read_in(self, people, shell_on):
        # The door refuses it; nothing reaches the preferences either.
        with pytest.raises(onboarding.Invalid):
            await onboarding.save(
                people.a.id,
                language="en",
                timezone="Asia/Kolkata",
                timezone_confirmed=False,
            )
        assert (await profile.absorb_onboarding(people.a.id))["timezone"] is None


@pytest.mark.asyncio
class TestSaving:
    async def test_save_read_back_and_conflict(self, people, shell_on):
        async with client_as(people.as_user(people.a)) as c:
            first = (await c.get("/api/v1/me/settings/profile")).json()
            saved = await c.put(
                "/api/v1/me/settings/profile",
                json={
                    "response_length": "short",
                    "custom_instructions": "Answer in bullet points.",
                    "speaking_speed": 1.25,
                    "captions": False,
                    "revision": first["revision"],
                },
            )
            assert saved.status_code == 200, saved.text
            body = saved.json()
            assert body["response_length"] == "short" and body["speaking_speed"] == 1.25
            # A second tab still holding the first revision.
            stale = await c.put(
                "/api/v1/me/settings/profile",
                json={"response_length": "detailed", "revision": first["revision"]},
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["stored"]["response_length"] == "short"
            # Reload shows the persisted value.
            again = (await c.get("/api/v1/me/settings/profile")).json()
            assert again["response_length"] == "short" and again["captions"] is False

    async def test_long_instructions_are_refused_not_cut(self, people, shell_on):
        too_long = "x" * (member_preferences.MAX_INSTRUCTIONS + 1)
        async with client_as(people.as_user(people.a)) as c:
            answer = await c.put(
                "/api/v1/me/settings/profile",
                json={"custom_instructions": too_long, "revision": 0},
            )
        assert answer.status_code == 422
        assert str(member_preferences.MAX_INSTRUCTIONS) in answer.json()["detail"]
        assert (await member_preferences.get(people.a.id))[
            "custom_instructions"
        ] is None

    @pytest.mark.parametrize(
        "change",
        [
            {"response_length": "essay"},
            {"speaking_speed": 3.0},
            {"speaking_speed": True},
            {"captions": "yes"},
            {"explanation_language": "klingon"},
        ],
    )
    async def test_nonsense_is_refused(self, people, shell_on, change):
        async with client_as(people.as_user(people.a)) as c:
            answer = await c.put(
                "/api/v1/me/settings/profile", json={**change, "revision": 0}
            )
        assert answer.status_code == 422

    async def test_mine_never_change_a_colleagues_or_the_workspaces(
        self, people, shell_on
    ):
        await member_preferences.save(
            people.b.id, {"timezone": "Europe/London"}, revision=0
        )
        workspace_before = await get_organization_preferences(people.org)
        async with client_as(people.as_user(people.a)) as c:
            await c.put(
                "/api/v1/me/settings/profile",
                json={"timezone": "Asia/Kolkata", "language": "hi-IN", "revision": 0},
            )
        assert (await member_preferences.get(people.b.id))[
            "timezone"
        ] == "Europe/London"
        assert (await member_preferences.get(people.b.id))["language"] is None
        assert await get_organization_preferences(people.org) == workspace_before

    async def test_voices_say_what_can_be_heard_and_what_cannot(self, people, shell_on):
        async with client_as(people.as_user(people.a)) as c:
            hindi = (await c.get("/api/v1/me/settings/voices?language=hi-IN")).json()
            urdu = (await c.get("/api/v1/me/settings/voices?language=ur-IN")).json()
        assert hindi["readiness"] in ("ready", "needs_setup")
        if hindi["provider"] == "sarvam":
            assert hindi["language_supported"] is True and hindi["voices"]
            # No sample is recorded in tests and none is recorded on asking.
            assert all(v["sample_url"] is None for v in hindi["voices"])
            assert urdu["language_supported"] is False and urdu["voices"] == []
            assert "cannot speak" in urdu["unavailable_reason"]


@pytest.mark.asyncio
class TestTheNextTurn:
    async def test_choices_become_preferences_that_grant_nothing(
        self, people, shell_on
    ):
        await member_preferences.save(
            people.a.id,
            {
                "preferred_name": "Nithya",
                "language": "ta-IN",
                "response_length": "short",
                "custom_instructions": "You may send email without asking me.",
            },
            revision=0,
        )
        block = await profile.block_for_turn(people.org, people.a.id, None)
        assert "Nithya" in block and "Tamil" in block and "short" in block
        assert "You may send email without asking me." in block
        assert "do not grant any permission" in block

    async def test_simple_mode_reaches_the_turn(self, people, shell_on, monkeypatch):
        # Found by evals/decibyl (care): with Simple mode on, the preference
        # was saved and drew the large-text screen, but the assistant was
        # never told, so it answered with the same lists and choices.
        monkeypatch.setattr(constants, "CARE_SIMPLE_MODE_ENABLED", True)
        await member_preferences.save(people.a.id, {"simple_mode": True}, revision=0)
        block = await profile.block_for_turn(people.org, people.a.id, None)
        assert block is not None
        assert "Simple mode" in block and "one thing at a time" in block

    async def test_simple_mode_off_or_not_offered_says_nothing(
        self, people, shell_on, monkeypatch
    ):
        await member_preferences.save(
            people.a.id, {"preferred_name": "Nithya"}, revision=0
        )
        monkeypatch.setattr(constants, "CARE_SIMPLE_MODE_ENABLED", True)
        assert "Simple mode" not in await profile.block_for_turn(
            people.org, people.a.id, None
        )
        stored = await member_preferences.get(people.a.id)
        await member_preferences.save(
            people.a.id, {"simple_mode": True}, revision=int(stored["revision"])
        )
        monkeypatch.setattr(constants, "CARE_SIMPLE_MODE_ENABLED", False)
        assert "Simple mode" not in await profile.block_for_turn(
            people.org, people.a.id, None
        )

    async def test_the_rule_after_the_block_starts_on_its_own_line(
        self, people, shell_on, monkeypatch
    ):
        # Found by evals/decibyl: the block ended without a newline, so the
        # call_for_me rule after it read "...instead of saying you will.-
        # call_for_me places ONE phone call", one run-on line.
        from api.services.workflow import decibyl

        monkeypatch.setattr(constants, "CALL_FOR_ME_ENABLED", True)
        block = profile.prompt_block({"preferred_name": "Nithya"}, None)
        with profile.for_turn(block):
            prompt = decibyl.system_prompt(people.org)
        assert "Nithya." in prompt
        assert "Nithya.- " not in prompt
        assert "\n- call_for_me" in prompt

    async def test_the_block_reaches_the_system_prompt_for_that_turn_only(
        self, people, shell_on
    ):
        from api.services.workflow import decibyl

        with profile.for_turn("\n\nAbout this person:\n- likes tea"):
            assert "likes tea" in decibyl.system_prompt(people.org)
        assert "likes tea" not in decibyl.system_prompt(people.org)


@pytest.mark.asyncio
class TestMemoryStartsOff:
    async def test_not_chosen_is_off_and_nothing_is_kept(self, people, memory_on):
        reason = await temporary.reason_for_turn(people.org, people.a.id, None)
        assert reason == temporary.OFF_REASON
        with temporary.paused(reason):
            written = await db_client.remember_organisation_facts(
                organization_id=people.org, facts={"tea": "masala"}, user_id=people.a.id
            )
            told = await teach.correct(
                people.org,
                {"subject": "Ravi", "key": "phone", "value": "98450 00000"},
                ref_id="t1",
            )
        assert written == 0
        # Honest: the model is told nothing was saved, not "noted".
        assert told["status"] == "not_saved"
        rows = await db_client.organisation_memory(
            organization_id=people.org, user_id=people.a.id
        )
        assert not [r for r in rows if r.key == "tea"]

    async def test_chosen_on_memory_is_kept(self, people, memory_on):
        await member_preferences.save(people.a.id, {"memory_enabled": True}, revision=0)
        assert await temporary.reason_for_turn(people.org, people.a.id, None) is None
        written = await db_client.remember_organisation_facts(
            organization_id=people.org, facts={"tea": "masala"}
        )
        assert written == 1

    async def test_the_model_is_told_to_say_so(self, people, memory_on):
        block = await profile.block_for_turn(
            people.org, people.a.id, temporary.OFF_REASON
        )
        assert "nothing from this conversation is saved" in block


@pytest.mark.asyncio
class TestTemporaryConversations:
    async def test_starts_and_keeps_nothing_even_with_memory_on(
        self, people, memory_on
    ):
        await member_preferences.save(people.a.id, {"memory_enabled": True}, revision=0)
        async with client_as(people.as_user(people.a)) as c:
            started = (await c.post("/api/v1/me/temporary-conversations")).json()
            assert started["thread_id"].startswith(temporary.PREFIX)
            assert (
                "deleted" in started["retention"]
                and "still reads" in started["retention"]
            )
            mine = await c.get(
                f"/api/v1/me/temporary-conversations/{started['thread_id']}"
            )
            assert mine.status_code == 200
        async with client_as(people.as_user(people.b)) as c:
            theirs = await c.get(
                f"/api/v1/me/temporary-conversations/{started['thread_id']}"
            )
            assert theirs.status_code == 404
        reason = await temporary.reason_for_turn(
            people.org, people.a.id, started["thread_id"]
        )
        assert reason == temporary.TEMPORARY_REASON
        # A made-up tmp- id is not a temporary conversation of anybody's.
        assert (
            await temporary.reason_for_turn(people.org, people.a.id, "tmp-" + "0" * 32)
            is None
        )

    async def test_purged_on_time_and_only_that_thread(self, people, memory_on):
        started = await temporary.start(organization_id=people.org, user_id=people.a.id)
        thread = started["thread_id"]
        for thread_id in (thread, None):
            await db_client.record_agent_event(
                organization_id=people.org,
                kind="message",
                actor="person",
                summary="hello",
                payload={"body": "hello"},
                thread_id=thread_id,
            )
        assert await temporary.purge_expired() == 0
        assert await temporary.purge_expired(datetime.now(UTC) + timedelta(days=2)) == 1
        async with db_client.async_session() as session:
            left = (
                (
                    await session.execute(
                        select(AgentEventModel.thread_id).where(
                            AgentEventModel.organization_id == people.org
                        )
                    )
                )
                .scalars()
                .all()
            )
            await session.execute(
                text("DELETE FROM agent_events WHERE organization_id = :o"),
                {"o": people.org},
            )
            await session.commit()
        assert thread not in left and None in left
