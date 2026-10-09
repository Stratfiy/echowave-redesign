"""Medicine reminder calls (launch stream care).

Done when: a reminder cannot be set up where calls cannot be placed (needs
setup, said before anything is saved); setting one up is a card showing the
masked number, times, language and who is told, that only the person can
answer; nothing rings until it is confirmed; each due dose is one row and
one call however many ticks run; the call goes through the platform's
outbound path, announced as Decibyl in the person's language, with the
do-not-call list honoured; a dose not taken, not answered or not placed
tells exactly the family named, once; "I took it" in the app stops the
call; pausing stops the calls at once; and dosing advice is refused.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.services.care import (
    CareError,
    NeedsSetup,
    calls,
    circle,
    medicines,
    reminder_call,
)
from api.services.workflow import actions
from api.tests import care_support as cs


@pytest.fixture
async def home(test_engine, monkeypatch):
    cs.all_on(monkeypatch)
    monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "taken")
    amma = await cs.person("med-amma")
    priya = await cs.person("med-priya")
    ravi = await cs.person("med-ravi")
    colleague = await cs.person("med-colleague")
    org = await cs.workspace(amma.id)
    await circle.set_display_name(org, amma.id, "Amma")
    members = {}
    for who, shares in ((priya, ["medicine_alerts"]), (ravi, ["medicine_schedule"])):
        made = await circle.propose_member(
            org,
            amma.id,
            name=who.email.split("-")[1].capitalize(),
            email=who.email,
            shares=shares,
        )
        payload = await cs.press(org, made["event_id"], amma.id)
        await circle.accept(who.id, payload["result"]["invite_code"])
        members[who.id] = made["member"]["id"]
    yield SimpleNamespace(
        amma=amma, priya=priya, ravi=ravi, colleague=colleague, org=org, members=members
    )
    await cs.cleanup(org)


async def _active(h, *, times=("08:00", "20:00"), tell=True):
    made = await medicines.propose(
        h.org,
        h.amma.id,
        label="BP tablet",
        times=list(times),
        phone="98765 43210",
        language="ta-IN",
        alert_member_ids=[h.members[h.priya.id]] if tell else [],
    )
    payload = await cs.press(h.org, made["event_id"], h.amma.id)
    assert payload["state"] == actions.DONE, payload
    return made["medicine"]["id"]


async def _doses(org):
    async with db_client.async_session() as session:
        return (
            await session.execute(
                text(
                    "SELECT id, state, reason, alerted_at, workflow_run_id, "
                    "outcome_history FROM care_dose_calls "
                    "WHERE organization_id = :o ORDER BY id"
                ),
                {"o": org},
            )
        ).all()


def _at(hour: int, minute: int = 0) -> datetime:
    """Today at that time in India, as UTC."""
    from zoneinfo import ZoneInfo

    local = datetime.now(ZoneInfo("Asia/Kolkata")).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return local.astimezone(UTC)


@pytest.mark.asyncio
class TestSetUp:
    async def test_needs_setup_when_no_line_and_says_so_first(self, home, monkeypatch):
        monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
        ready = await calls.readiness(home.org)
        assert ready["state"] == "needs_setup" and "phone line" in ready["reason"]
        with pytest.raises(NeedsSetup):
            await medicines.propose(
                home.org,
                home.amma.id,
                label="BP tablet",
                times=["08:00"],
                phone="9876543210",
            )
        assert await medicines.list_mine(home.org, home.amma.id) == []
        async with cs.client(home.amma.id, home.org) as c:
            r = await c.post(
                "/api/v1/care/medicines",
                json={"label": "BP tablet", "times": ["08:00"], "phone": "9876543210"},
            )
            assert r.status_code == 409 and r.json()["detail"]["state"] == "needs_setup"
            status = await c.get("/api/v1/care/status")
            parts = {p["key"]: p["state"] for p in status.json()["parts"]}
            # Reminders in Decibyl need no number, so the part is available;
            # it is phone calls that wait for a line.
            assert parts["care_medicine_calls"] == "available"

    async def test_test_mode_is_never_offered_in_production(self, home, monkeypatch):
        for environment in ("production", "staging", "something-new"):
            monkeypatch.setattr(constants, "ENVIRONMENT", environment)
            assert calls.fake_mode() is None
            assert (await calls.readiness(home.org))["state"] == "needs_setup"
        monkeypatch.setattr(constants, "ENVIRONMENT", "test")
        assert (await calls.readiness(home.org))["state"] == "test_mode"

    async def test_the_card_shows_exactly_what_will_ring(self, home):
        made = await medicines.propose(
            home.org,
            home.amma.id,
            label="BP tablet",
            times=["20:00", "08:00"],
            phone="98765 43210",
            language="ta-IN",
            alert_member_ids=[home.members[home.priya.id]],
        )
        card = (
            await db_client.get_agent_event(made["event_id"], organization_id=home.org)
        ).payload
        assert card["action"] == actions.CARE_MEDICINE_CALLS
        assert card["only_user_id"] == home.amma.id
        assert (
            card["label"]
            == "Reminder calls for BP tablet: every day at 08:00 and 20:00, in Tamil"
        )
        assert "ending 3210" in card["effect"] and "9876543210" not in card["effect"]
        assert "say it is Decibyl" in card["effect"]
        assert "never gives advice about doses" in card["effect"]
        assert "Priya will be told" in card["effect"]
        assert card["args"]["phone"] == "+919876543210"
        assert made["medicine"]["state"] == medicines.AWAITING
        # Nothing rings before the person confirms.
        assert await calls.tick(_at(8, 1)) == 0

    async def test_only_the_person_confirms_and_a_no_never_rings(self, home):
        made = await medicines.propose(
            home.org,
            home.amma.id,
            label="BP tablet",
            times=["08:00"],
            phone="9876543210",
        )
        with pytest.raises(actions.ActionError):
            await cs.press(home.org, made["event_id"], home.colleague.id)
        await cs.press(home.org, made["event_id"], home.amma.id, verb="decline")
        meds = await medicines.list_mine(home.org, home.amma.id)
        assert meds[0]["state"] == medicines.DECLINED
        assert await calls.tick(_at(8, 1)) == 0

    @pytest.mark.parametrize(
        "label",
        [
            "How much paracetamol should I take",
            "double the dose if pain",
            "what dose of insulin",
        ],
    )
    async def test_dosing_advice_is_refused(self, home, label):
        with pytest.raises(CareError, match="only reminds"):
            medicines.clean_label(label)

    async def test_family_named_must_share_alerts(self, home):
        with pytest.raises(CareError, match="missed-medicine alerts"):
            await medicines.propose(
                home.org,
                home.amma.id,
                label="BP tablet",
                times=["08:00"],
                phone="9876543210",
                alert_member_ids=[home.members[home.ravi.id]],
            )

    @pytest.mark.parametrize(
        "times",
        [[], ["8am"], ["25:00"], ["08:00"] * 1 + [f"0{i}:00" for i in range(1, 8)]],
    )
    async def test_times_are_checked(self, home, times):
        with pytest.raises(CareError):
            medicines.clean_times(times)


@pytest.mark.asyncio
class TestCalls:
    async def test_one_dose_one_call_however_many_ticks(self, home):
        await _active(home)
        now = _at(8, 2)
        placed = (
            await calls.tick(now)
            + await calls.tick(now)
            + await calls.tick(now + timedelta(minutes=1))
        )
        assert placed == 1
        rows = await _doses(home.org)
        assert len(rows) == 1 and rows[0].state == calls.TAKEN

    async def test_long_past_doses_are_not_rung(self, home):
        await _active(home)
        assert await calls.tick(_at(8, 30)) == 0

    async def test_not_taken_tells_exactly_the_family_named_once(
        self, home, monkeypatch
    ):
        monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "not_taken")
        await _active(home)
        await calls.tick(_at(20, 1))
        rows = await _doses(home.org)
        assert rows[0].state == calls.NOT_TAKEN and rows[0].alerted_at is not None
        priya = (await circle.family_view(home.priya.id))[0]
        assert [a["title"] for a in priya["alerts"]] == [
            "Amma said they had not taken BP tablet at the 20:00 reminder call."
        ]
        # Ravi sees the schedule (shared) but is not told (not named, no alerts share).
        ravi = (await circle.family_view(home.ravi.id))[0]
        assert ravi["alerts"] == []
        assert ravi["medicines"][0]["doses"][0]["state"] == calls.NOT_TAKEN
        assert "phone" not in str(ravi["medicines"]) and "3210" not in str(ravi)
        assert await calls.tell_family(rows[0].id) == 0

    async def test_unanswered_calls_are_swept_and_the_family_told(
        self, home, monkeypatch
    ):
        await _active(home)
        with patch.object(calls, "_dial", new=AsyncMock(return_value=4242)):
            monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
            await calls.tick(_at(8, 1))
        rows = await _doses(home.org)
        assert rows[0].state == calls.CALLING
        # "Did not answer" only on evidence: the carrier's no-answer, as the
        # status webhook writes it onto the call's run.
        no_answer = SimpleNamespace(
            is_completed=True,
            answered_at=None,
            billable_seconds=None,
            gathered_context={
                "call_tags": ["not_connected", "telephony_no-answer"],
                "mapped_call_disposition": "no-answer",
            },
        )
        with patch.object(
            db_client, "get_workflow_run", new=AsyncMock(return_value=no_answer)
        ):
            assert await calls.sweep(datetime.now(UTC) + timedelta(minutes=5)) == 0
            assert await calls.sweep(datetime.now(UTC) + timedelta(hours=1)) == 1
        rows = await _doses(home.org)
        assert rows[0].state == calls.NOT_ANSWERED
        titles = [
            a["title"] for a in (await circle.family_view(home.priya.id))[0]["alerts"]
        ]
        assert titles == ["Amma did not answer the 08:00 reminder call for BP tablet."]

    async def test_a_call_that_cannot_be_placed_is_said_not_hidden(
        self, home, monkeypatch
    ):
        await _active(home)
        monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
        await calls.tick(_at(8, 1))
        rows = await _doses(home.org)
        assert rows[0].state == calls.FAILED and rows[0].reason == "needs_setup"
        titles = [
            a["title"] for a in (await circle.family_view(home.priya.id))[0]["alerts"]
        ]
        assert titles == [
            "Decibyl could not call Amma about BP tablet at 08:00: reminder calls need a phone line."
        ]

    async def test_i_took_it_in_the_app_means_no_call(self, home):
        medicine_id = await _active(home)
        await medicines.mark_taken(home.org, home.amma.id, medicine_id, due_at=_at(8))
        assert await calls.tick(_at(8, 1)) == 0
        with pytest.raises(CareError):
            await medicines.mark_taken(
                home.org, home.amma.id, medicine_id, due_at=_at(9, 13)
            )

    async def test_pausing_stops_calls_and_resuming_is_a_new_card(self, home):
        medicine_id = await _active(home)
        await medicines.pause(home.org, home.amma.id, medicine_id)
        assert await calls.tick(_at(8, 1)) == 0
        resumed = await medicines.resume(home.org, home.amma.id, medicine_id)
        assert resumed["event_id"] and resumed["medicine"]["state"] == medicines.PAUSED
        await cs.press(home.org, resumed["event_id"], home.amma.id)
        assert await calls.tick(_at(8, 1)) == 1

    async def test_undo_turns_the_calls_off(self, home):
        made = await medicines.propose(
            home.org,
            home.amma.id,
            label="BP tablet",
            times=["08:00"],
            phone="9876543210",
        )
        await cs.press(home.org, made["event_id"], home.amma.id)
        await cs.press(home.org, made["event_id"], home.amma.id, verb="undo")
        assert (await medicines.list_mine(home.org, home.amma.id))[0][
            "state"
        ] == medicines.PAUSED
        assert await calls.tick(_at(8, 1)) == 0

    async def test_off_means_nothing_rings(self, home, monkeypatch):
        await _active(home)
        monkeypatch.setattr(constants, "CARE_MEDICINE_CALLS_ENABLED", False)
        assert await calls.tick(_at(8, 1)) == 0
        assert await calls.sweep(datetime.now(UTC) + timedelta(hours=2)) == 0


class TestTheRealCallPath:
    @pytest.mark.asyncio
    async def test_dial_goes_through_the_platform_outbound_path_announced_as_decibyl(
        self, home, monkeypatch
    ):
        medicine_id = await _active(home)
        monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
        config = SimpleNamespace(id=77)
        dialled = AsyncMock(return_value=555)
        dnd = AsyncMock(return_value="+919876543210")
        with (
            patch.object(
                db_client,
                "get_default_telephony_configuration",
                new=AsyncMock(return_value=config),
            ),
            patch("api.services.compliance.dnd.assert_may_call", new=dnd),
            patch(
                "api.services.telephony.factory.get_telephony_provider_by_id",
                new=AsyncMock(return_value=SimpleNamespace(PROVIDER_NAME="fake")),
            ),
            patch("api.services.telephony.outbound.dial_workflow", new=dialled),
        ):
            assert (await calls.readiness(home.org))["state"] == "ready"
            await calls.tick(_at(8, 1))
        kwargs = dialled.await_args.kwargs
        assert kwargs["source"] == "care_reminder"
        assert kwargs["telephony_configuration_id"] == 77
        assert kwargs["to_number"] == "+919876543210"
        context = kwargs["extra_context"]
        assert context["care_medicine"] == "BP tablet"
        assert context["care_language_name"] == "Tamil"
        assert "Decibyl" in context["care_greeting"]
        assert context["care_greeting"].startswith("வணக்கம் Amma")
        assert kwargs["workflow"].workflow_configurations[reminder_call.MARK] is True
        # The do-not-call list is checked; the person asked for these times.
        assert dnd.await_args.kwargs["enforce_calling_hours"] is False
        rows = await _doses(home.org)
        assert rows[0].state == calls.CALLING
        # The agent is made once and reused.
        again = await reminder_call.ensure_workflow(home.org, user_id=home.amma.id)
        assert again.id == kwargs["workflow"].id
        del medicine_id

    @pytest.mark.asyncio
    async def test_do_not_call_list_refuses_and_tells_family(self, home, monkeypatch):
        await _active(home)
        monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
        from api.services.compliance import dnd

        with (
            patch.object(
                db_client,
                "get_default_telephony_configuration",
                new=AsyncMock(return_value=SimpleNamespace(id=1)),
            ),
            patch.object(
                dnd,
                "assert_may_call",
                new=AsyncMock(side_effect=dnd.DoNotDisturbListed("listed")),
            ),
        ):
            await calls.tick(_at(8, 1))
        rows = await _doses(home.org)
        assert rows[0].state == calls.FAILED and rows[0].reason == "do_not_call"

    @pytest.mark.parametrize(
        "gathered, annotations, expected",
        [
            ({"extracted_variables": {"dose_taken": "taken"}}, {}, calls.TAKEN),
            ({"extracted_variables": {"dose_taken": "not_yet"}}, {}, calls.NOT_TAKEN),
            ({}, {"disposition": {"code": "no_answer"}}, calls.NOT_ANSWERED),
            ({"mapped_call_disposition": "busy"}, {}, calls.NOT_ANSWERED),
            ({}, {}, calls.NOT_ANSWERED),
            ({"extracted_variables": {"dose_taken": "maybe"}}, {}, calls.UNCLEAR),
        ],
    )
    def test_what_a_finished_call_means(self, gathered, annotations, expected):
        run = SimpleNamespace(gathered_context=gathered, annotations=annotations)
        assert calls.outcome_of(run) == expected

    @pytest.mark.asyncio
    async def test_post_call_settles_only_its_own_workspaces_dose(
        self, home, monkeypatch
    ):
        await _active(home)
        with patch.object(calls, "_dial", new=AsyncMock(return_value=999)):
            monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
            await calls.tick(_at(8, 1))
        dose_id = (await _doses(home.org))[0].id
        run = SimpleNamespace(
            initial_context={"care_dose_id": dose_id},
            gathered_context={"extracted_variables": {"dose_taken": "taken"}},
            annotations={},
        )
        with (
            patch.object(
                db_client, "get_workflow_run", new=AsyncMock(return_value=run)
            ),
            patch.object(
                db_client,
                "get_organization_id_by_workflow_run_id",
                new=AsyncMock(return_value=home.org + 100000),
            ),
        ):
            await calls.record_run_outcome(999)
        assert (await _doses(home.org))[0].state == calls.CALLING
        with (
            patch.object(
                db_client, "get_workflow_run", new=AsyncMock(return_value=run)
            ),
            patch.object(
                db_client,
                "get_organization_id_by_workflow_run_id",
                new=AsyncMock(return_value=home.org),
            ),
        ):
            await calls.record_run_outcome(999)
        assert (await _doses(home.org))[0].state == calls.TAKEN

    def test_every_call_language_has_a_greeting_naming_decibyl(self):
        for tag in medicines.LANGUAGE_NAMES:
            line = reminder_call.greeting(tag, medicine="BP tablet", person="Amma")
            assert "Decibyl" in line and "BP tablet" in line and "Amma" in line
        assert reminder_call.greeting("hi-IN", medicine="X").startswith("नमस्ते,")

    def test_the_call_script_reminds_and_never_advises(self):
        prompt = reminder_call._RULES.lower()
        assert "never say how much to take" in prompt
        assert "never ask for, and never accept, an otp" in prompt


@pytest.mark.asyncio
async def test_nobody_else_sees_or_changes_my_reminders(home):
    medicine_id = await _active(home)
    assert await medicines.list_mine(home.org, home.colleague.id) == []
    with pytest.raises(CareError):
        await medicines.pause(home.org, home.colleague.id, medicine_id)
    other = await cs.workspace(home.colleague.id)
    try:
        async with cs.client(home.colleague.id, other) as c:
            assert (await c.get("/api/v1/care/medicines")).json()["medicines"] == []
            r = await c.post(f"/api/v1/care/medicines/{medicine_id}/pause")
            assert r.status_code == 404
    finally:
        await cs.cleanup(other)


@pytest.mark.asyncio
async def test_list_over_http_shows_test_mode_honestly(home):
    await _active(home)
    async with cs.client(home.amma.id, home.org) as c:
        body = (await c.get("/api/v1/care/medicines")).json()
    assert body["calls"]["state"] == "test_mode"
    assert "nobody is rung" in body["calls"]["reason"]
    med = body["medicines"][0]
    assert med["state"] == "active" and med["phone_masked"] == "the number ending 3210"
    assert "phone" not in med


@pytest.mark.asyncio
class TestEdit:
    """A reminder can be changed and removed. What the person confirmed is
    what rings: a running reminder stops on an edit until the new card is
    confirmed, and the number is never editable."""

    async def test_editing_a_running_reminder_stops_it_until_confirmed(self, home):
        medicine_id = await _active(home)
        made = await medicines.edit(
            home.org,
            home.amma.id,
            medicine_id,
            label="BP tablet after food",
            times=["09:00"],
        )
        med = made["medicine"]
        assert med["state"] == medicines.PAUSED
        assert med["label"] == "BP tablet after food" and med["times"] == ["09:00"]
        assert made["event_id"]
        # Not running: the old 08:00 never rings.
        assert await calls.tick(_at(8, 1)) == 0
        payload = await cs.press(home.org, made["event_id"], home.amma.id)
        assert payload["state"] == actions.DONE, payload
        (row,) = await medicines.list_mine(home.org, home.amma.id)
        assert row["state"] == medicines.ACTIVE and row["times"] == ["09:00"]

    async def test_the_old_card_cannot_start_changed_details(self, home):
        made = await medicines.propose(
            home.org,
            home.amma.id,
            label="BP tablet",
            times=["08:00"],
            phone="98765 43210",
            language="ta-IN",
        )
        await medicines.edit(
            home.org, home.amma.id, made["medicine"]["id"], times=["21:00"]
        )
        old = await cs.press(home.org, made["event_id"], home.amma.id)
        assert old["state"] != actions.DONE
        (row,) = await medicines.list_mine(home.org, home.amma.id)
        assert row["state"] != medicines.ACTIVE

    async def test_a_paused_reminder_just_saves(self, home):
        medicine_id = await _active(home)
        await medicines.pause(home.org, home.amma.id, medicine_id)
        made = await medicines.edit(
            home.org, home.amma.id, medicine_id, language="hi-IN"
        )
        assert made["event_id"] is None
        assert made["medicine"]["state"] == medicines.PAUSED
        assert made["medicine"]["language"] == "hi-IN"

    async def test_nothing_changed_changes_nothing(self, home):
        medicine_id = await _active(home)
        made = await medicines.edit(
            home.org, home.amma.id, medicine_id, label="BP tablet"
        )
        assert made["event_id"] is None
        assert made["medicine"]["state"] == medicines.ACTIVE

    async def test_dosing_advice_is_still_refused_on_edit(self, home):
        medicine_id = await _active(home)
        with pytest.raises(CareError):
            await medicines.edit(
                home.org, home.amma.id, medicine_id, label="how much should I take"
            )

    async def test_remove_stops_and_hides_it(self, home):
        medicine_id = await _active(home)
        await medicines.remove(home.org, home.amma.id, medicine_id)
        assert await medicines.list_mine(home.org, home.amma.id) == []
        assert await calls.tick(_at(8, 1)) == 0

    async def test_over_http_and_only_mine(self, home):
        medicine_id = await _active(home)
        other = await cs.workspace(home.colleague.id)
        try:
            async with cs.client(home.colleague.id, other) as c:
                r = await c.patch(
                    f"/api/v1/care/medicines/{medicine_id}", json={"times": ["10:00"]}
                )
                assert r.status_code == 404
                assert (
                    await c.delete(f"/api/v1/care/medicines/{medicine_id}")
                ).status_code == 404
        finally:
            await cs.cleanup(other)
        async with cs.client(home.amma.id, home.org) as c:
            r = await c.patch(
                f"/api/v1/care/medicines/{medicine_id}", json={"phone": "99999 11111"}
            )
            assert r.status_code == 422
            r = await c.patch(
                f"/api/v1/care/medicines/{medicine_id}", json={"times": ["10:00"]}
            )
            assert r.status_code == 200, r.text
            assert r.json()["medicine"]["times"] == ["10:00"]
            assert r.json()["card"] is not None
            assert (
                await c.delete(f"/api/v1/care/medicines/{medicine_id}")
            ).status_code == 204
            assert (await c.get("/api/v1/care/medicines")).json()["medicines"] == []
