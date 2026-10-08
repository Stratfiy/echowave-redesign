"""The family circle (launch stream care): family see only what the older
person consented to share.

Done when: adding someone is a consent card only the older person can
answer; nothing is shared before it is confirmed and accepted; the code
works only for the invited email, once; the family view carries exactly the
shared kinds (and not the others, the phone number or the words of a scam
check); sharing more is another card, sharing less and removing are
immediate and hide past alerts; and nobody outside reads any of it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from api.db import db_client
from api.services.care import CareError, NotFound, circle, scam
from api.services.workflow import actions
from api.tests import care_support as cs

CODE = re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{4}")


@pytest.fixture
async def family(test_engine, monkeypatch):
    cs.all_on(monkeypatch)
    amma = await cs.person("amma")
    priya = await cs.person("priya")
    colleague = await cs.person("colleague")
    stranger = await cs.person("stranger")
    org = await cs.workspace(amma.id)
    other = await cs.workspace(stranger.id)
    await circle.set_display_name(org, amma.id, "Amma")
    yield amma, priya, colleague, stranger, org, other
    await cs.cleanup(org, other)


async def _invite(org, amma, priya, shares):
    made = await circle.propose_member(
        org, amma.id, name="Priya", email=priya.email, shares=shares
    )
    payload = await cs.press(org, made["event_id"], amma.id)
    assert payload["state"] == actions.DONE, payload
    code = payload["result"]["invite_code"]
    return made, code


@pytest.mark.asyncio
class TestConsent:
    async def test_adding_someone_is_a_card_and_nothing_is_shared_before_it(
        self, family
    ):
        amma, priya, *_, org, _ = family
        made = await circle.propose_member(
            org, amma.id, name="Priya", email=priya.email, shares=["medicine_alerts"]
        )
        card = await db_client.get_agent_event(made["event_id"], organization_id=org)
        payload = card.payload
        assert payload["action"] == actions.CARE_FAMILY_INVITE
        assert payload["state"] == actions.PROPOSED
        assert payload["only_user_id"] == amma.id
        assert "Priya" in payload["label"] and priya.email not in payload["label"]
        assert "missed or not answered" in payload["effect"]
        assert payload.get("version")
        member = (await circle.my_circle(org, amma.id))["members"][0]
        assert member["status"] == "proposed" and member["shares"] == []
        assert await circle.family_view(priya.id) == []

    async def test_only_the_older_person_can_answer_it(self, family):
        amma, priya, colleague, *_, org, _ = family
        made = await circle.propose_member(
            org, amma.id, name="Priya", email=priya.email, shares=["medicine_alerts"]
        )
        with pytest.raises(actions.ActionError, match="not here"):
            await cs.press(org, made["event_id"], colleague.id)
        card = await db_client.get_agent_event(made["event_id"], organization_id=org)
        assert card.payload["state"] == actions.PROPOSED

    async def test_declining_shares_nothing(self, family):
        amma, priya, *_, org, _ = family
        made = await circle.propose_member(
            org, amma.id, name="Priya", email=priya.email, shares=["medicine_alerts"]
        )
        await cs.press(org, made["event_id"], amma.id, verb="decline")
        assert (await circle.my_circle(org, amma.id))["members"] == []

    async def test_confirm_makes_a_code_for_that_email_only(self, family):
        amma, priya, _, stranger, org, _ = family
        _, code = await _invite(org, amma, priya, ["medicine_alerts"])
        assert CODE.fullmatch(code)
        with pytest.raises(CareError, match="different email"):
            await circle.accept(stranger.id, code)
        with pytest.raises(CareError, match="own email"):
            await circle.propose_member(
                org, amma.id, name="Me", email=amma.email, shares=["medicine_alerts"]
            )
        accepted = await circle.accept(priya.id, code.lower().replace("-", ""))
        assert accepted == {"person": "Amma", "shares": ["medicine_alerts"]}
        with pytest.raises(NotFound):
            await circle.accept(priya.id, code)

    async def test_an_expired_code_does_not_work(self, family):
        amma, priya, *_, org, _ = family
        _, code = await _invite(org, amma, priya, ["medicine_alerts"])
        async with db_client.async_session() as session:
            await session.execute(
                text(
                    "UPDATE care_circle_members SET invite_expires_at = :t "
                    "WHERE organization_id = :o"
                ),
                {"t": datetime.now(UTC) - timedelta(minutes=1), "o": org},
            )
            await session.commit()
        with pytest.raises(CareError, match="expired"):
            await circle.accept(priya.id, code)

    async def test_unknown_shares_are_refused_not_dropped(self, family):
        amma, priya, *_, org, _ = family
        with pytest.raises(CareError, match="can be shared"):
            await circle.propose_member(
                org, amma.id, name="Priya", email=priya.email, shares=["location"]
            )


@pytest.mark.asyncio
class TestWhatFamilySee:
    async def test_exactly_what_was_shared(self, family):
        amma, priya, *_, org, _ = family
        _, code = await _invite(org, amma, priya, ["medicine_alerts"])
        await circle.accept(priya.id, code)
        await scam.check(
            org, amma.id, text="Tell me the OTP now or your account is blocked"
        )
        told = await circle.alert(
            org,
            amma.id,
            kind="dose_missed",
            share="medicine_alerts",
            title="Amma did not answer the 08:00 reminder call for BP tablet.",
            subject_id=1,
        )
        assert told == 1
        view = await circle.family_view(priya.id)
        assert len(view) == 1
        entry = view[0]
        assert entry["person"] == "Amma"
        # What must appear.
        assert [a["title"] for a in entry["alerts"]] == [
            "Amma did not answer the 08:00 reminder call for BP tablet."
        ]
        # What must not: medicines and scam checks were not shared.
        assert entry["medicines"] is None and entry["scam_checks"] is None
        assert all(a["kind"] != "scam_checked" for a in entry["alerts"])

    async def test_scam_checks_shared_show_the_verdict_never_the_words(self, family):
        amma, priya, *_, org, _ = family
        _, code = await _invite(org, amma, priya, ["scam_checks"])
        await circle.accept(priya.id, code)
        secret = "Share OTP 482913 with me to unblock your SBI account"
        await scam.check(org, amma.id, text=secret)
        entry = (await circle.family_view(priya.id))[0]
        assert entry["scam_checks"][0]["verdict"] == "likely_scam"
        assert "asks_for_otp" in entry["scam_checks"][0]["signals"]
        assert any(a["kind"] == "scam_checked" for a in entry["alerts"])
        assert "482913" not in str(entry) and "SBI account" not in str(entry)
        async with db_client.async_session() as session:
            stored = (
                await session.execute(
                    text("SELECT * FROM care_scam_checks WHERE organization_id = :o"),
                    {"o": org},
                )
            ).all()
        assert "482913" not in str(stored)

    async def test_sharing_more_is_a_card_sharing_less_is_now(self, family):
        amma, priya, *_, org, _ = family
        made, code = await _invite(org, amma, priya, ["medicine_alerts"])
        await circle.accept(priya.id, code)
        member_id = made["member"]["id"]
        wider = await circle.change_shares(
            org, amma.id, member_id, ["medicine_alerts", "medicine_schedule"]
        )
        assert wider["event_id"]
        # Not yet: still only the alerts.
        assert (await circle.family_view(priya.id))[0]["medicines"] is None
        await cs.press(org, wider["event_id"], amma.id)
        assert (await circle.family_view(priya.id))[0]["medicines"] == []
        narrower = await circle.change_shares(
            org, amma.id, member_id, ["medicine_schedule"]
        )
        assert narrower["event_id"] is None
        assert (await circle.family_view(priya.id))[0]["shares"] == [
            "medicine_schedule"
        ]

    async def test_removing_someone_hides_everything_at_once(self, family):
        amma, priya, *_, org, _ = family
        made, code = await _invite(org, amma, priya, ["medicine_alerts"])
        await circle.accept(priya.id, code)
        await circle.alert(
            org,
            amma.id,
            kind="dose_missed",
            share="medicine_alerts",
            title="Amma missed a dose.",
            subject_id=2,
        )
        assert (await circle.family_view(priya.id))[0]["alerts"]
        await circle.revoke(org, amma.id, made["member"]["id"])
        assert await circle.family_view(priya.id) == []
        assert (
            await circle.alert(
                org,
                amma.id,
                kind="dose_missed",
                share="medicine_alerts",
                title="Amma missed another.",
                subject_id=3,
            )
            == 0
        )

    async def test_undo_takes_the_invitation_back(self, family):
        amma, priya, *_, org, _ = family
        made, code = await _invite(org, amma, priya, ["medicine_alerts"])
        await cs.press(org, made["event_id"], amma.id, verb="undo")
        with pytest.raises(NotFound):
            await circle.accept(priya.id, code)


@pytest.mark.asyncio
class TestNobodyElse:
    async def test_a_colleague_cannot_change_or_read_someone_elses_circle(self, family):
        amma, priya, colleague, _, org, _ = family
        made, _ = await _invite(org, amma, priya, ["medicine_alerts"])
        with pytest.raises(NotFound):
            await circle.revoke(org, colleague.id, made["member"]["id"])
        assert (await circle.my_circle(org, colleague.id))["members"] == []

    async def test_another_workspace_cannot_reach_it(self, family):
        amma, priya, _, stranger, org, other = family
        made, _ = await _invite(org, amma, priya, ["medicine_alerts"])
        with pytest.raises(NotFound):
            await circle.revoke(other, amma.id, made["member"]["id"])
        async with cs.client(stranger.id, other) as c:
            r = await c.delete(f"/api/v1/care/circle/members/{made['member']['id']}")
            assert r.status_code == 404
            assert (await c.get("/api/v1/care/family")).json() == {"people": []}

    async def test_alerts_cannot_be_marked_by_someone_else(self, family):
        amma, priya, _, stranger, org, _ = family
        _, code = await _invite(org, amma, priya, ["medicine_alerts"])
        await circle.accept(priya.id, code)
        await circle.alert(
            org,
            amma.id,
            kind="dose_missed",
            share="medicine_alerts",
            title="Amma missed a dose.",
            subject_id=9,
        )
        alert_id = (await circle.family_view(priya.id))[0]["alerts"][0]["id"]
        with pytest.raises(NotFound):
            await circle.mark_alert_read(stranger.id, alert_id)
        await circle.mark_alert_read(priya.id, alert_id)
        assert (await circle.family_view(priya.id))[0]["alerts"][0]["read"] is True


@pytest.mark.asyncio
async def test_the_whole_journey_over_http(family):
    amma, priya, *_, org, _ = family
    async with cs.client(amma.id, org) as c:
        added = await c.post(
            "/api/v1/care/circle/members",
            json={"name": "Priya", "email": priya.email, "shares": ["medicine_alerts"]},
        )
        assert added.status_code == 200, added.text
        body = added.json()
        assert body["card"]["payload"]["action"] == actions.CARE_FAMILY_INVITE
        assert body["member"]["status"] == "proposed"
        bad = await c.post(
            "/api/v1/care/circle/members",
            json={"name": "X", "email": "not-an-email", "shares": ["medicine_alerts"]},
        )
        assert bad.status_code == 422
    payload = await cs.press(org, body["card"]["id"], amma.id)
    code = CODE.search(payload["done"]["note"]).group(0)
    assert "Email is not set up here" in payload["done"]["note"]
    async with cs.client(priya.id, org) as c:
        joined = await c.post("/api/v1/care/family/accept", json={"code": code})
        assert joined.status_code == 200 and joined.json()["person"] == "Amma"
        people = (await c.get("/api/v1/care/family")).json()["people"]
        assert people[0]["shares"] == ["medicine_alerts"]
        assert people[0]["medicines"] is None


@pytest.mark.asyncio
async def test_a_care_card_is_read_back_only_by_its_person(family):
    amma, priya, colleague, stranger, org, other = family
    made = await circle.propose_member(
        org, amma.id, name="Priya", email=priya.email, shares=["medicine_alerts"]
    )
    async with cs.client(amma.id, org) as c:
        r = await c.get(f"/api/v1/care/cards/{made['event_id']}")
        assert r.status_code == 200 and r.json()["payload"]["state"] == "proposed"
    async with cs.client(colleague.id, org) as c:
        assert (
            await c.get(f"/api/v1/care/cards/{made['event_id']}")
        ).status_code == 404
    async with cs.client(stranger.id, other) as c:
        assert (
            await c.get(f"/api/v1/care/cards/{made['event_id']}")
        ).status_code == 404


@pytest.mark.asyncio
async def test_no_card_means_nothing_left_waiting(family, monkeypatch):
    amma, priya, *_, org, _ = family

    async def refuse(**_):
        return {"status": "not_proposed", "reason": "no organisation"}

    monkeypatch.setattr(actions, "propose", refuse)
    with pytest.raises(CareError):
        await circle.propose_member(
            org, amma.id, name="Priya", email=priya.email, shares=["medicine_alerts"]
        )
    assert (await circle.my_circle(org, amma.id))["members"] == []
    monkeypatch.undo()
    cs.all_on(monkeypatch)
    again = await circle.propose_member(
        org, amma.id, name="Priya", email=priya.email, shares=["medicine_alerts"]
    )
    assert again["event_id"]
