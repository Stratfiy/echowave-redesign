"""Personal adaptation (services/personal, ``evolve_personal``).

Done when:

* a preference said outright is saved at once in the person's own store,
  with the line it came from and when, and shown on a card in the thread;
* it reaches their turns, and code applies it where they are the one rung
  or reminded -- the language of a "call me when it's done" call, the hours
  it may ring, a reminder's channel -- with no model involved;
* saying it again differently supersedes it and keeps the history;
  Correct does the same from the card; Forget deletes it and its history;
* "what do you know about me" is a card in the thread, with no model turn;
* "Not quite: too long" offers a preference on a card; nothing is kept
  until the person presses Save;
* the context control lists what a conversation uses and leaves a source
  out of that person's turns in that conversation only;
* a colleague in the same workspace, and anybody in another, never reads
  any of it -- not by id, not on the timeline, not through workspace memory;
* a kept preference never widens the calling window, changes who is rung,
  or touches the daily cap; and with the flag off nothing changes at all.

No call is placed: ``calls._dial`` is replaced where a tick runs, and
``dial_workflow`` is replaced where the dial's own context is checked.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from api import constants
from api.db import db_client
from api.db.models import AgentEventModel, OrganisationFactModel
from api.db.personal_models import PersonalPreferenceModel
from api.enums import AgentEventActor, AgentEventKind
from api.services import call_when_done as cwd
from api.services.call_when_done import allowance, calls, number, optin
from api.services.identity import notifications
from api.services.knowledge_graph import personal as personal_memory
from api.services.personal import capture, cards, context_control, preferences
from api.services.workflow import agent_timeline, decibyl, tasks_board
from api.tests import care_support as cs
from api.tests.support.settings_people import client_as

IST = ZoneInfo("Asia/Kolkata")
PHONE = "+919876543210"


def _ist(hour: int, minute: int = 0, *, days: int = 0) -> datetime:
    local = datetime(2026, 10, 12, hour, minute, tzinfo=IST) + timedelta(days=days)
    return local.astimezone(UTC)


def _as(who, org: int):
    return SimpleNamespace(
        id=who.id,
        selected_organization_id=org,
        email=f"user{who.id}@example.test",
        email_verified_at=None,
        mfa_enabled=False,
        mfa_secret_encrypted=None,
        mfa_last_counter=None,
        password_hash=None,
    )


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(constants, "EVOLVE_PERSONAL_ENABLED", True)


@pytest.fixture
async def home(test_engine, monkeypatch):
    monkeypatch.setattr(constants, "CALL_WHEN_DONE_ENABLED", True)
    monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
    monkeypatch.setattr(constants, "CALL_WHEN_DONE_GATHER_SECONDS", 60)
    asha = await cs.person("evp-asha")
    colleague = await cs.person("evp-colleague")
    bob = await cs.person("evp-bob")
    org = await cs.workspace(asha.id)
    other_org = await cs.workspace(bob.id)
    for who, where in ((asha, org), (colleague, org), (bob, other_org)):
        await db_client.add_user_to_organization(who.id, where)
    line = AsyncMock(return_value=SimpleNamespace(id=7))
    monkeypatch.setattr(db_client, "get_default_telephony_configuration", line)
    dial = AsyncMock(return_value=4242)
    monkeypatch.setattr(calls, "_dial", dial)
    monkeypatch.setattr(notifications, "notify", AsyncMock(return_value={}))
    clock = {"now": _ist(11, 0)}
    monkeypatch.setattr(calls, "_now", lambda: clock["now"])
    yield SimpleNamespace(
        asha=asha,
        colleague=colleague,
        bob=bob,
        org=org,
        other_org=other_org,
        dial=dial,
        clock=clock,
    )
    async with db_client.async_session() as session:
        users = [asha.id, colleague.id, bob.id]
        await session.execute(
            text("DELETE FROM personal_preferences WHERE user_id = ANY(:u)"),
            {"u": users},
        )
        await session.execute(
            text("DELETE FROM member_preferences WHERE user_id = ANY(:u)"),
            {"u": users},
        )
        for o in (org, other_org):
            for table in (
                "conversation_context_choices",
                "output_feedback",
                "done_callbacks",
                "done_calls",
                "done_call_numbers",
                "agent_tasks",
                "organisation_facts",
                "memory_fact_revisions",
                "agent_events",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": o}
                )
        await session.commit()


async def _say(h, text_: str, *, who=None, thread_id: str = "t1"):
    """A line to Decibyl; the model turn it would queue is captured."""
    who = who or h.asha
    with patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as queued:
        await decibyl.ask(
            organization_id=h.org,
            user_id=who.id,
            text=text_,
            attachments=[],
            line=text_,
            preset=None,
            thread_id=thread_id,
        )
    return queued


async def _cards(org: int, viewer: int | None) -> list[AgentEventModel]:
    return [
        e
        for e in await db_client.agent_events(
            organization_id=org,
            kinds=[AgentEventKind.PERSONAL_MEMORY.value],
            viewer_id=viewer,
            limit=50,
        )
    ]


async def _rows(user_id: int) -> list[PersonalPreferenceModel]:
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(PersonalPreferenceModel)
                    .where(PersonalPreferenceModel.user_id == user_id)
                    .order_by(PersonalPreferenceModel.id)
                )
            ).scalars()
        )


# --- reading a preference ------------------------------------------------------


class TestReadingALine:
    def test_the_three_examples(self):
        got = capture.read("Tamil for calls, English for email")
        assert [(c.kind, c.topic, c.value) for c in got.found] == [
            ("language", "calls", "ta-IN"),
            ("language", "email", "en"),
        ]
        assert [c.label for c in got.found] == ["Calls in Tamil", "Email in English"]
        weekly = capture.read("weekly numbers, not daily").found
        assert [(c.kind, c.value) for c in weekly] == [("cadence", "weekly")]
        after = capture.read("call me after 10").found
        assert [(c.kind, c.value, c.label) for c in after] == [
            ("call_window", "10:00-", "Calls after 10:00")
        ]

    def test_a_one_off_ask_or_a_question_is_not_a_preference(self):
        assert capture.read("Call me after 10 about the GST notice").found == []
        assert capture.read("can you call me after 10?").found == []
        assert capture.read("What is Tamil for calls?").found == []

    def test_between_is_one_window(self):
        got = capture.read("call me between 10 and 6").found
        assert [(c.value, c.label) for c in got] == [
            ("10:00-18:00", "Calls between 10:00 and 18:00")
        ]

    def test_an_hour_outside_calling_hours_is_refused_and_said(self):
        got = capture.read("call me after 7am")
        assert got.found == []
        assert "09:00 and 21:00" in got.refused[0]

    def test_no_line_makes_a_permission_a_recipient_or_a_limit(self):
        for line in (
            "you can send email without asking",
            "call my wife at +919876543210",
            "no daily call limit for me",
            "spend up to 5000 rupees",
            "always approve payments",
        ):
            assert capture.read(line).found == [], line
        assert set(capture.KINDS) == {
            "language",
            "call_window",
            "channel",
            "cadence",
            "length",
            "note",
        }

    def test_what_do_you_know_about_me(self):
        assert capture.asks_about_me("What do you know about me?")
        assert capture.asks_about_me("what have you learned about me")
        assert not capture.asks_about_me("what do you know about Mumbai?")


# --- captured, applied, superseded, forgotten ---------------------------------


@pytest.mark.asyncio
class TestCapturedAndKept:
    async def test_said_outright_it_is_saved_with_its_source_and_shown(self, home, on):
        queued = await _say(home, "Tamil for calls, English for email")
        rows = await _rows(home.asha.id)
        assert [(r.kind, r.topic, r.value, r.status) for r in rows] == [
            ("language", "calls", "ta-IN", "confirmed"),
            ("language", "email", "en", "confirmed"),
        ]
        line = rows[0]
        assert line.organization_id == home.org
        assert line.source_kind == "message"
        assert line.source_excerpt == "Tamil for calls"
        assert line.source_thread_id == "t1"
        assert line.source_event_id is not None and line.observed_at is not None
        # The card, on her thread, naming the rows; the model still answers.
        shown = await _cards(home.org, home.asha.id)
        assert len(shown) == 1 and shown[0].payload["view"] == "saved"
        assert shown[0].payload["ids"] == [r.id for r in rows]
        assert shown[0].payload["private_to"] == home.asha.id
        queued.assert_awaited()

    async def test_said_differently_it_supersedes_and_keeps_the_history(self, home, on):
        await _say(home, "Tamil for calls")
        await _say(home, "actually, Hindi for calls")
        rows = await _rows(home.asha.id)
        assert [(r.value, r.status) for r in rows] == [
            ("ta-IN", "superseded"),
            ("hi-IN", "confirmed"),
        ]
        assert rows[1].supersedes_id == rows[0].id and rows[0].superseded_at
        history = await preferences.history(
            user_id=home.asha.id, preference_id=rows[1].id
        )
        assert [h["label"] for h in history] == ["Calls in Tamil"]
        # Saying the same thing again changes nothing.
        await _say(home, "Hindi for calls")
        assert len(await _rows(home.asha.id)) == 2

    async def test_correct_from_the_card_and_forget(self, home, on):
        await _say(home, "call me after 10")
        (row,) = await _rows(home.asha.id)
        async with client_as(_as(home.asha, home.org)) as c:
            wrong = await c.post(
                f"/api/v1/personal/preferences/{row.id}/correct",
                json={"text": "purple"},
            )
            assert wrong.status_code == 422
            fixed = await c.post(
                f"/api/v1/personal/preferences/{row.id}/correct",
                json={"text": "11:30"},
            )
            assert fixed.status_code == 200, fixed.text
            new = fixed.json()["preference"]
            assert new["label"] == "Calls after 11:30"
            assert new["source"]["kind"] == "correction"
            assert fixed.json()["replaced"]["value"] == "10:00-"
            gone = await c.delete(f"/api/v1/personal/preferences/{new['id']}")
            assert gone.status_code == 200 and gone.json()["forgotten"] == 2
            listed = (await c.get("/api/v1/personal/preferences")).json()
            assert listed["preferences"] == []
        # Deleted, history and all.
        assert await _rows(home.asha.id) == []

    async def test_about_me_is_a_card_and_no_model_turn(self, home, on):
        await _say(home, "remind me on WhatsApp")
        await db_client.remember_organisation_facts(
            organization_id=home.org, facts={"tea": "masala"}, user_id=home.asha.id
        )
        queued = await _say(home, "What do you know about me?")
        queued.assert_not_called()
        about = [
            e
            for e in await _cards(home.org, home.asha.id)
            if e.payload["view"] == "about_me"
        ]
        assert len(about) == 1
        async with client_as(_as(home.asha, home.org)) as c:
            drawn = (await c.get(f"/api/v1/personal/cards/{about[0].id}")).json()
        assert [p["label"] for p in drawn["preferences"]] == ["Reminders on WhatsApp"]
        assert drawn["preferences"][0]["source"]["excerpt"] == "remind me on WhatsApp"
        assert drawn["preferences"][0]["observed_at"]
        assert [item["key"] for item in drawn["learned"]] == ["tea"]

    async def test_a_learned_item_is_corrected_with_history_and_forgotten(
        self, home, on
    ):
        await db_client.remember_organisation_facts(
            organization_id=home.org, facts={"tea": "masala"}, user_id=home.asha.id
        )
        about = await cards.about_me(organization_id=home.org, user_id=home.asha.id)
        fact_id = about["learned"][0]["id"]
        async with client_as(_as(home.asha, home.org)) as c:
            fixed = await c.post(
                f"/api/v1/personal/learned/{fact_id}/correct",
                json={"text": "ginger, no sugar"},
            )
            assert fixed.status_code == 200, fixed.text
            assert fixed.json()["history"][0]["before"] == "masala"
            assert (
                await c.delete(f"/api/v1/personal/learned/{fact_id}")
            ).status_code == 200
        async with db_client.async_session() as session:
            assert await session.get(OrganisationFactModel, fact_id) is None

    async def test_in_a_temporary_or_memory_off_conversation_nothing_is_kept(
        self, home, on, monkeypatch
    ):
        monkeypatch.setattr(constants, "MEMORY_MANAGER_ENABLED", True)
        # Memory not chosen yet: off, so nothing is saved.
        await _say(home, "Tamil for calls")
        assert await _rows(home.asha.id) == []
        assert await _cards(home.org, home.asha.id) == []


# --- applied deterministically --------------------------------------------------


async def _confirm_number(h, phone: str = PHONE) -> None:
    event_id = await number.propose(h.org, h.asha.id, phone, thread_id="t1")
    payload = await cs.press(h.org, event_id, h.asha.id)
    assert payload["state"] == "done", payload


async def _finish(h) -> None:
    task = await db_client.create_task(
        organization_id=h.org,
        title="Deploy the site",
        brief="",
        status=tasks_board.IN_PROGRESS,
        created_by=h.asha.id,
        depth=0,
    )
    await optin.opt_in(h.org, h.asha.id, thread_id="t1")
    with patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
        await tasks_board._finish(
            task.id,
            organization_id=h.org,
            status=tasks_board.DONE,
            result="Deployed.",
            run_id=None,
            from_id=None,
            assignee_name="Decibyl",
            title=task.title,
        )


async def _calls(org: int):
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    text("SELECT * FROM done_calls WHERE organization_id = :o"),
                    {"o": org},
                )
            ).mappings()
        )


async def _dial_context(h, monkeypatch) -> dict:
    """What a real ``_dial`` hands the dialler for Asha's queued call."""
    from api.services.call_when_done import agent
    from api.services.telephony import factory, outbound

    seen: dict = {}

    async def fake_dial(**kwargs):
        seen.update(kwargs)
        return 99

    monkeypatch.setattr(outbound, "dial_workflow", fake_dial)
    monkeypatch.setattr(
        factory, "get_telephony_provider_by_id", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        agent, "ensure_workflow", AsyncMock(return_value=SimpleNamespace(id=1))
    )
    call = SimpleNamespace(id=1, organization_id=h.org, user_id=h.asha.id)
    with patch.object(calls, "items_of_call", AsyncMock(return_value=[])):
        await calls.__dict__["_real_dial"](call, PHONE)
    return seen


@pytest.fixture
def real_dial(monkeypatch):
    """Keep a handle on the real ``_dial`` before ``home`` replaces it."""
    monkeypatch.setitem(calls.__dict__, "_real_dial", calls._dial)


@pytest.mark.asyncio
class TestApplied:
    async def test_the_call_speaks_the_language_she_asked_for_calls(
        self, real_dial, home, on, monkeypatch
    ):
        from api.services import member_preferences

        await member_preferences.save(home.asha.id, {"language": "en"}, revision=0)
        await _say(home, "Tamil for calls, English for email")
        seen = await _dial_context(home, monkeypatch)
        assert seen["extra_context"]["done_language"] == "ta"
        assert seen["extra_context"]["done_language_name"] == "Tamil"
        # Who is rung is the confirmed number, whatever is kept.
        assert seen["to_number"] == PHONE

    async def test_flag_off_the_call_speaks_her_usual_language(
        self, real_dial, home, monkeypatch
    ):
        from api.services import member_preferences

        await member_preferences.save(home.asha.id, {"language": "en"}, revision=0)
        await preferences.save(
            user_id=home.asha.id,
            organization_id=home.org,
            candidate=capture.read("Tamil for calls").found[0],
        )
        seen = await _dial_context(home, monkeypatch)
        assert seen["extra_context"]["done_language"] == "en"

    async def test_call_me_after_10_holds_a_finish_at_915_until_10(self, home, on):
        await _confirm_number(home)
        await _say(home, "call me after 10")
        home.clock["now"] = _ist(9, 15)
        await _finish(home)
        (call,) = await _calls(home.org)
        assert call["due_at"] == _ist(10, 0)
        assert await calls.tick(_ist(9, 30)) == 0
        assert await calls.tick(_ist(10, 0) + timedelta(seconds=5)) == 1
        home.dial.assert_awaited_once()

    async def test_flag_off_the_same_finish_rings_at_916(self, home, monkeypatch):
        await _confirm_number(home)
        await preferences.save(
            user_id=home.asha.id,
            organization_id=home.org,
            candidate=capture.read("call me after 10").found[0],
        )
        home.clock["now"] = _ist(9, 15)
        await _finish(home)
        (call,) = await _calls(home.org)
        assert call["due_at"] == _ist(9, 16)

    async def test_a_due_call_is_held_at_the_dial_too(self, home, on):
        """Queued before the preference was said: the dial still holds it."""
        await _confirm_number(home)
        home.clock["now"] = _ist(9, 15)
        await _finish(home)
        await _say(home, "call me after 10")
        assert await calls.tick(_ist(9, 20)) == 1  # claimed, then held
        home.dial.assert_not_awaited()
        (call,) = await _calls(home.org)
        assert call["state"] == cwd.QUEUED and call["due_at"] == _ist(10, 0)

    async def test_a_reminder_goes_on_her_channel_unless_she_picks_one(
        self, home, on, monkeypatch
    ):
        from api.services.today import reminders
        from api.services.today.scope import Viewer

        await _say(home, "remind me on WhatsApp")
        viewer = Viewer(
            user_id=home.asha.id, organization_id=home.org, zone_name="Asia/Kolkata"
        )
        filled = await reminders.their_channel(viewer, {"title": "x", "channel": None})
        assert filled["channel"] == "whatsapp"
        named = await reminders.their_channel(viewer, {"title": "x", "channel": "push"})
        assert named["channel"] == "push"
        # Somebody else's draft is theirs.
        other = Viewer(
            user_id=home.colleague.id,
            organization_id=home.org,
            zone_name="Asia/Kolkata",
        )
        assert (await reminders.their_channel(other, {"title": "x"})).get(
            "channel"
        ) is None

    async def test_weekly_numbers_set_the_span_of_her_turns_only(self, home, on):
        from api.services import acting

        await _say(home, "weekly numbers, not daily")
        assert await preferences.cadence(home.asha.id, home.org) == "weekly"
        assert await preferences.cadence(home.colleague.id, home.org) is None
        with acting.acting_as(home.asha.id):
            hers = await decibyl.build_context(home.org, "how are the numbers")
        assert "in the last 7 days" in hers
        with acting.acting_as(home.colleague.id):
            theirs = await decibyl.build_context(home.org, "how are the numbers")
        assert "in the last 7 days" not in theirs
        # Named in the question, the question wins.
        with acting.acting_as(home.asha.id):
            today = await decibyl.build_context(home.org, "numbers today")
        assert "in the last 7 days" not in today
        # Personal left out of the conversation: her cadence is not read.
        with (
            acting.acting_as(home.asha.id),
            context_control.for_turn({context_control.PERSONAL}),
        ):
            left = await decibyl.build_context(home.org, "how are the numbers")
        assert "in the last 7 days" not in left

    async def test_the_turn_is_told_her_preferences_as_preferences(self, home, on):
        await _say(home, "Tamil for calls")
        await _say(home, "remember that I am vegetarian")
        block = await preferences.block_for_turn(home.org, home.asha.id)
        assert "Calls in Tamil" in block and "I am vegetarian" in block
        assert "grant no permission" in block
        with preferences.for_turn(block):
            prompt = decibyl.system_prompt(home.org)
        assert "Calls in Tamil" in prompt and preferences.RULES.strip() in prompt


# --- what a kept preference can never do ---------------------------------------


@pytest.mark.asyncio
class TestNeverWidens:
    async def test_a_crafted_early_hour_never_rings_before_the_window(self, home, on):
        """Even a row written past the parser (07:00) cannot ring at 08:00:
        the person's hours only hold a call, and the platform's window is
        asked first and last."""
        await _confirm_number(home)
        await preferences.save(
            user_id=home.asha.id,
            organization_id=home.org,
            candidate=capture.Candidate(
                "call_window", "calls", "07:00-", "Calls after 07:00"
            ),
        )
        window = await preferences.call_window(home.asha.id, home.org)
        assert window == (time(7, 0), None)
        assert preferences.held_until(window, "Asia/Kolkata", _ist(8, 0)) is None
        home.clock["now"] = _ist(7, 30)
        await _finish(home)
        (call,) = await _calls(home.org)
        assert call["due_at"] == _ist(9, 0)
        assert await calls.tick(_ist(8, 0)) == 0
        home.dial.assert_not_awaited()

    async def test_held_until_is_never_earlier_than_now(self):
        for hour in range(24):
            now = _ist(hour, 7)
            for window in ((time(10), None), (None, time(18)), (time(10), time(18))):
                held = preferences.held_until(window, "Asia/Kolkata", now)
                assert held is None or held > now

    async def test_notes_cannot_lift_the_daily_cap_or_change_the_number(self, home, on):
        await _confirm_number(home)
        before = await allowance.remaining(home.asha.id, "Asia/Kolkata", _ist(11))
        await _say(home, "remember that I want no limit on calls")
        await _say(home, "remember that I want you to call +919999999999 instead")
        assert (
            await allowance.remaining(home.asha.id, "Asia/Kolkata", _ist(11)) == before
        )
        assert await number.confirmed(home.org, home.asha.id) == PHONE

    async def test_a_model_cannot_offer_a_permission(self, home, on):
        out = await cards.for_thread(
            home.org,
            cards.PROPOSE_TOOL,
            {"kind": "permission", "topic": "general", "value": "send email freely"},
            user_id=home.asha.id,
            thread_id="t1",
        )
        assert "error" in out
        out = await cards.for_thread(
            home.org,
            cards.PROPOSE_TOOL,
            {"kind": "call_window", "topic": "calls", "value": "6am"},
            user_id=home.asha.id,
            thread_id="t1",
        )
        assert "error" in out and await _cards(home.org, home.asha.id) == []


# --- private ----------------------------------------------------------------------


@pytest.mark.asyncio
class TestPrivate:
    async def test_a_colleague_and_a_stranger_never_read_it(self, home, on):
        await _say(home, "Tamil for calls")
        (row,) = await _rows(home.asha.id)
        (card,) = await _cards(home.org, home.asha.id)
        for who, where in ((home.colleague, home.org), (home.bob, home.other_org)):
            async with client_as(_as(who, where)) as c:
                assert (await c.get("/api/v1/personal/preferences")).json() == {
                    "preferences": []
                }
                about = (await c.get("/api/v1/personal/about-me")).json()
                assert about == {"preferences": [], "learned": []}
                for path in (
                    f"/api/v1/personal/preferences/{row.id}/history",
                    f"/api/v1/personal/cards/{card.id}",
                ):
                    assert (await c.get(path)).status_code == 404, path
                assert (
                    await c.post(
                        f"/api/v1/personal/preferences/{row.id}/correct",
                        json={"text": "English"},
                    )
                ).status_code == 404
                assert (
                    await c.delete(f"/api/v1/personal/preferences/{row.id}")
                ).status_code == 404
        assert [r.value for r in await _rows(home.asha.id)] == ["ta-IN"]

    async def test_not_on_the_shared_timeline(self, home, on):
        await _say(home, "Tamil for calls")
        assert len(await _cards(home.org, home.asha.id)) == 1
        assert await _cards(home.org, home.colleague.id) == []
        assert await _cards(home.org, None) == []

    async def test_never_in_workspace_memory_or_a_colleagues_turn(self, home, on):
        await _say(home, "Tamil for calls, English for email")
        await _say(home, "remember that I am vegetarian")
        every = await db_client.organisation_memory(
            organization_id=home.org, include_bots=True, user_id=home.colleague.id
        )
        values = {str(r.value) for r in every} | {str(r.key) for r in every}
        assert not values & {"ta-IN", "en", "I am vegetarian", "Calls in Tamil"}
        async with db_client.async_session() as session:
            leaked = await session.scalar(
                select(OrganisationFactModel).where(
                    OrganisationFactModel.value.in_(["ta-IN", "I am vegetarian"])
                )
            )
        assert leaked is None
        # A colleague's turn and the workspace's recall read none of it.
        assert await preferences.block_for_turn(home.org, home.colleague.id) is None
        assert (
            await preferences.language_for(home.colleague.id, "calls", home.org) is None
        )
        # Decibyl's own reading of memory for a colleague's question.
        from api.services import acting

        with acting.acting_as(home.colleague.id):
            rows = await db_client.organisation_memory(
                organization_id=home.org,
                kind="fact",
                status="confirmed",
                user_id=personal_memory.viewer(),
            )
        assert "vegetarian" not in decibyl.memory_block(rows)

    async def test_the_memory_manager_shows_a_colleague_nothing_of_it(self, home, on):
        from api.services.settings import memory

        await _say(home, "remember that I am vegetarian")
        seen = await memory.overview(
            organization_id=home.org, user_id=home.colleague.id
        )
        assert "vegetarian" not in str(seen)

    async def test_a_proposal_is_answered_by_its_owner_only(self, home, on):
        reply_id = await agent_timeline.record(
            organization_id=home.org,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary="A very long answer",
            payload={"body": "A very long answer", "from": "Decibyl"},
            in_channel=False,
            thread_id="t1",
        )
        card_id = await cards.from_feedback(
            organization_id=home.org,
            user_id=home.asha.id,
            reply_event_id=reply_id,
            reasons=["too_long"],
        )
        async with client_as(_as(home.colleague, home.org)) as c:
            refused = await c.post(
                f"/api/v1/personal/cards/{card_id}/answer", json={"save": True}
            )
            assert refused.status_code == 404
        assert await _rows(home.colleague.id) == []
        assert await _rows(home.asha.id) == []


# --- feedback -> a preference, never silently -----------------------------------


@pytest.mark.asyncio
class TestFeedback:
    async def _reply(self, h, asked: str) -> int:
        await agent_timeline.record(
            organization_id=h.org,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.HUMAN.value,
            summary=asked,
            payload={"body": asked, "author_id": h.asha.id, "to": "Decibyl"},
            in_channel=False,
            thread_id="t1",
        )
        return await agent_timeline.record(
            organization_id=h.org,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary="Here is a long answer in English",
            payload={"body": "Here is a long answer in English", "from": "Decibyl"},
            in_channel=False,
            thread_id="t1",
        )

    async def test_not_quite_wrong_language_offers_a_card_and_keeps_nothing(
        self, home, on, monkeypatch
    ):
        from api.services import feedback

        monkeypatch.setattr(constants, "REPLY_FEEDBACK_ENABLED", True, raising=False)
        reply_id = await self._reply(home, "இன்றைய அழைப்புகள் எத்தனை?")
        await feedback.submit(
            organization_id=home.org,
            user_id=home.asha.id,
            subject_kind="reply",
            subject_id=reply_id,
            verdict="not_quite",
            reasons=["wrong_language"],
        )
        assert await _rows(home.asha.id) == []  # nothing kept yet
        (card,) = await _cards(home.org, home.asha.id)
        assert card.payload["view"] == "proposal" and card.payload["state"] == "open"
        assert card.payload["proposal"]["label"] == "Replies in Tamil"
        async with client_as(_as(home.asha, home.org)) as c:
            saved = await c.post(
                f"/api/v1/personal/cards/{card.id}/answer", json={"save": True}
            )
            assert saved.status_code == 200 and saved.json()["state"] == "saved"
            again = await c.post(
                f"/api/v1/personal/cards/{card.id}/answer", json={"save": True}
            )
            assert again.status_code == 409
        (row,) = await _rows(home.asha.id)
        assert (row.kind, row.value, row.source_kind) == (
            "language",
            "ta-IN",
            "feedback",
        )
        assert row.review_at is not None  # offered, so asked about again later

    async def test_no_thanks_keeps_nothing_and_wrong_offers_nothing(self, home, on):
        reply_id = await self._reply(home, "how many calls today")
        none = await cards.from_feedback(
            organization_id=home.org,
            user_id=home.asha.id,
            reply_event_id=reply_id,
            reasons=["wrong", "irrelevant"],
        )
        assert none is None
        card_id = await cards.from_feedback(
            organization_id=home.org,
            user_id=home.asha.id,
            reply_event_id=reply_id,
            reasons=["too_long"],
        )
        async with client_as(_as(home.asha, home.org)) as c:
            done = await c.post(
                f"/api/v1/personal/cards/{card_id}/answer", json={"save": False}
            )
            assert done.json()["state"] == "dismissed"
        assert await _rows(home.asha.id) == []

    async def test_the_rules_never_say_ratings_train_the_model(self):
        assert "do not train you" in preferences.RULES


# --- the context control ------------------------------------------------------------


@pytest.mark.asyncio
class TestContextControl:
    async def test_the_chip_and_leaving_personal_out_of_one_conversation(
        self, home, on, monkeypatch
    ):
        monkeypatch.setattr(constants, "PERSONAL_MEMORY_ENABLED", True)
        await _say(home, "Tamil for calls")
        async with client_as(_as(home.asha, home.org)) as c:
            first = (await c.get("/api/v1/personal/context?thread_id=t1")).json()
            assert first["chip"].startswith("Personal · Tamil")
            ids = [s["id"] for s in first["sources"]]
            assert ids[:4] == ["personal", "workspace", "knowledge", "files"]
            out = await c.put(
                "/api/v1/personal/context",
                json={"thread_id": "t1", "source": "personal", "included": False},
            )
            assert out.status_code == 200
            assert not out.json()["chip"].startswith("Personal")
            assert out.json()["left_out"] == 1
            bad = await c.put(
                "/api/v1/personal/context",
                json={"thread_id": "t1", "source": "everything", "included": False},
            )
            assert bad.status_code == 422
        left = await context_control.excluded_for(home.org, home.asha.id, "t1")
        assert left == {"personal"}
        # Only that conversation, only her.
        assert await context_control.excluded_for(home.org, home.asha.id, "t2") == set()
        assert (
            await context_control.excluded_for(home.org, home.colleague.id, "t1")
            == set()
        )
        # In that conversation her personal memory is not read for the turn.
        from api.services import acting

        with acting.acting_as(home.asha.id), context_control.for_turn(left):
            assert personal_memory.viewer() is None
        with acting.acting_as(home.asha.id):
            assert personal_memory.viewer() == home.asha.id

    async def test_an_app_left_out_is_out_of_the_turns_tools(
        self, home, on, monkeypatch
    ):
        from api.services.workflow import connected_tools

        app = SimpleNamespace(
            id=1,
            tool_uuid="t-cal",
            name="Calendar events",
            description="",
            category="composio",
            status="active",
            definition={
                "type": "composio",
                "config": {
                    "tool_slug": "GOOGLECALENDAR_FIND_EVENT",
                    "toolkit": "googlecalendar",
                },
            },
        )
        monkeypatch.setattr(
            connected_tools, "list_for_organization", AsyncMock(return_value=[app])
        )

        def names(tools):
            return {(t.get("function") or t).get("name") for t in tools}

        kept = await decibyl.tools_for(home.org, {})
        with context_control.for_turn({"app:googlecalendar"}):
            left = await decibyl.tools_for(home.org, {})
        assert connected_tools.function_name(app) in names(kept) - names(left)


# --- off ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOff:
    async def test_flag_off_nothing_is_captured_or_shown_and_routes_are_404(self, home):
        queued = await _say(home, "Tamil for calls")
        queued.assert_awaited()
        queued = await _say(home, "what do you know about me?")
        queued.assert_awaited()  # an ordinary turn, as before
        assert await _rows(home.asha.id) == []
        assert await _cards(home.org, home.asha.id) == []
        async with client_as(_as(home.asha, home.org)) as c:
            for path in (
                "/api/v1/personal/preferences",
                "/api/v1/personal/about-me",
                "/api/v1/personal/context",
            ):
                assert (await c.get(path)).status_code == 404
        tools = {
            (t.get("function") or t).get("name") for t in decibyl.office_tools(home.org)
        }
        assert cards.SHOW_TOOL not in tools and cards.PROPOSE_TOOL not in tools
        assert preferences.RULES not in decibyl.system_prompt(home.org)
        assert await context_control.excluded_for(home.org, home.asha.id, "t1") == set()
