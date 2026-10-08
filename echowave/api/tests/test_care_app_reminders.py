"""Medicine reminders work with no phone number (phase 3, care).

Staging showed adding a reminder refused with "needs a phone line" on a
workspace without a number, which broke the rule that every feature works on
Free with no number. Done when: with no line, a reminder can still be set up
to come in Decibyl (no phone asked for), through the same consent card; at
the due time it is shown and sent on the person's notification channels
once; "I took it" settles it; no confirmation in time tells the family named;
and a phone-call reminder on a workspace with no line is still refused, with
the way past in the words.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.services.care import NeedsSetup, calls, circle, medicines
from api.services.workflow import actions
from api.tests import care_support as cs


def _at(hour: int, minute: int = 0) -> datetime:
    from zoneinfo import ZoneInfo

    local = datetime.now(ZoneInfo("Asia/Kolkata")).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return local.astimezone(UTC)


@pytest.fixture
async def home(test_engine, monkeypatch):
    cs.all_on(monkeypatch)
    monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
    amma = await cs.person("app-amma")
    priya = await cs.person("app-priya")
    org = await cs.workspace(amma.id)
    await circle.set_display_name(org, amma.id, "Amma")
    made = await circle.propose_member(
        org, amma.id, name="Priya", email=priya.email, shares=["medicine_alerts"]
    )
    payload = await cs.press(org, made["event_id"], amma.id)
    await circle.accept(priya.id, payload["result"]["invite_code"])
    yield SimpleNamespace(
        amma=amma, priya=priya, org=org, priya_member=made["member"]["id"]
    )
    await cs.cleanup(org)


async def _doses(org):
    async with db_client.async_session() as session:
        return (
            await session.execute(
                text(
                    "SELECT id, state, reason, alerted_at FROM care_dose_calls "
                    "WHERE organization_id = :o ORDER BY id"
                ),
                {"o": org},
            )
        ).all()


@pytest.mark.asyncio
async def test_with_no_number_a_reminder_can_still_be_set_up(home):
    assert (await calls.readiness(home.org))["state"] == "needs_setup"
    made = await medicines.propose(
        home.org,
        home.amma.id,
        label="BP tablet",
        times=["08:00"],
        channel="app",
        alert_member_ids=[home.priya_member],
    )
    card = (
        await db_client.get_agent_event(made["event_id"], organization_id=home.org)
    ).payload
    assert card["action"] == actions.CARE_MEDICINE_CALLS
    assert "remind you in Decibyl" in card["effect"]
    assert "ring" not in card["effect"]
    assert "Priya will be told" in card["effect"]
    payload = await cs.press(home.org, made["event_id"], home.amma.id)
    assert payload["state"] == actions.DONE, payload
    mine = await medicines.list_mine(home.org, home.amma.id)
    assert mine[0]["channel"] == "app" and mine[0]["state"] == "active"
    assert mine[0]["phone_masked"] is None


@pytest.mark.asyncio
async def test_a_call_with_no_number_is_refused_with_the_way_past(home):
    with pytest.raises(NeedsSetup) as refused:
        await medicines.propose(
            home.org,
            home.amma.id,
            label="BP tablet",
            times=["08:00"],
            phone="9876543210",
            channel="call",
        )
    assert "remind you in Decibyl" in str(refused.value)
    async with cs.client(home.amma.id, home.org) as c:
        body = (await c.get("/api/v1/care/medicines")).json()
    assert body["calls"]["state"] == "needs_setup"
    assert body["app"]["state"] == "ready"


@pytest.mark.asyncio
async def test_due_reminder_is_sent_once_and_unconfirmed_tells_the_family(home):
    made = await medicines.propose(
        home.org,
        home.amma.id,
        label="BP tablet",
        times=["08:00"],
        channel="app",
        alert_member_ids=[home.priya_member],
    )
    await cs.press(home.org, made["event_id"], home.amma.id)
    notify = AsyncMock(return_value={"push": "ok"})
    with patch("api.services.identity.notifications.notify", new=notify):
        placed = await calls.tick(_at(8, 1)) + await calls.tick(_at(8, 2))
    assert placed == 1
    assert notify.await_count == 1
    kwargs = notify.await_args.kwargs
    assert kwargs["topic"] == "reminders" and "BP tablet" in kwargs["body"]
    assert notify.await_args.args[0] == home.amma.id
    rows = await _doses(home.org)
    assert rows[0].state == calls.REMINDED
    assert await calls.sweep(datetime.now(UTC) + timedelta(hours=1)) == 1
    rows = await _doses(home.org)
    assert rows[0].state == calls.NOT_ANSWERED and rows[0].alerted_at is not None
    titles = [
        a["title"] for a in (await circle.family_view(home.priya.id))[0]["alerts"]
    ]
    assert titles == ["Amma did not confirm taking BP tablet at 08:00."]


@pytest.mark.asyncio
async def test_i_took_it_settles_an_app_reminder(home):
    made = await medicines.propose(
        home.org, home.amma.id, label="BP tablet", times=["08:00"], channel="app"
    )
    await cs.press(home.org, made["event_id"], home.amma.id)
    medicine_id = made["medicine"]["id"]
    with patch(
        "api.services.identity.notifications.notify", new=AsyncMock(return_value={})
    ):
        await calls.tick(_at(8, 1))
    await medicines.mark_taken(home.org, home.amma.id, medicine_id, due_at=_at(8))
    assert await calls.sweep(datetime.now(UTC) + timedelta(hours=1)) == 0
    assert (await _doses(home.org))[0].state == calls.TAKEN


@pytest.mark.asyncio
async def test_an_app_reminder_over_http_needs_no_phone(home):
    async with cs.client(home.amma.id, home.org) as c:
        r = await c.post(
            "/api/v1/care/medicines",
            json={"label": "BP tablet", "times": ["08:00"], "channel": "app"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["card"]["payload"]["action"] == actions.CARE_MEDICINE_CALLS
        bad = await c.post(
            "/api/v1/care/medicines",
            json={"label": "BP tablet", "times": ["08:00"], "channel": "call"},
        )
        assert bad.status_code == 422  # a call needs a phone to ring
