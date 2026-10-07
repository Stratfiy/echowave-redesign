"""Tech help (launch stream care): one step at a time, with "did that work?".

Done when: every guide is plain and complete; a question finds its guide
and an unknown one says so and offers the topics (never a made-up answer);
yes moves on, no shows another way, no again stops kindly and tells the
family who share help requests (and only them); a double tap answers once;
and a session is its person's own.
"""

from __future__ import annotations

import pytest

from api.services.care import NotFound, circle, guides, tech_help
from api.tests import care_support as cs


def test_every_guide_is_plain_and_complete():
    assert len(guides.GUIDES) >= 6
    slugs = set()
    for guide in guides.GUIDES:
        assert guide.slug not in slugs
        slugs.add(guide.slug)
        assert guide.keywords and guide.steps
        for step in guide.steps:
            assert step.say.strip() and len(step.say) <= 260
            assert step.instead is None or len(step.instead) <= 260
            # One thing at a time: never a list crammed into one step.
            assert step.say.count("\n") == 0


@pytest.mark.parametrize(
    "question, slug",
    [
        ("how do I make the writing bigger", "bigger_text"),
        ("my phone ring is too quiet, I cannot hear it", "louder_ring"),
        ("video call my grandson on whatsapp", "whatsapp_video_call"),
        ("stop spam calls from a number", "block_number"),
    ],
)
def test_questions_find_their_guide(question, slug):
    assert guides.match(question)[0].slug == slug


@pytest.fixture
async def amma(test_engine, monkeypatch):
    cs.all_on(monkeypatch)
    amma = await cs.person("help-amma")
    priya = await cs.person("help-priya")
    ravi = await cs.person("help-ravi")
    stranger = await cs.person("help-stranger")
    org = await cs.workspace(amma.id)
    await circle.set_display_name(org, amma.id, "Amma")
    for who, shares in ((priya, ["help_requests"]), (ravi, ["medicine_alerts"])):
        made = await circle.propose_member(
            org,
            amma.id,
            name=who.email.split("-")[1].capitalize(),
            email=who.email,
            shares=shares,
        )
        payload = await cs.press(org, made["event_id"], amma.id)
        await circle.accept(who.id, payload["result"]["invite_code"])
    yield amma, priya, ravi, stranger, org
    await cs.cleanup(org)


@pytest.mark.asyncio
async def test_unknown_question_says_so_and_offers_topics(amma):
    person, *_, org = amma
    started = await tech_help.start(org, person.id, question="fix my car engine")
    assert started["matched"] is False
    assert "do not have steps" in started["note"]
    assert {t["slug"] for t in started["topics"]} == {g.slug for g in guides.GUIDES}


@pytest.mark.asyncio
async def test_yes_moves_on_until_done(amma):
    person, *_, org = amma
    started = (await tech_help.start(org, person.id, guide="screenshot"))["session"]
    assert started["step_number"] == 1 and started["steps_total"] == 2
    step = await tech_help.answer(
        org, person.id, started["id"], worked=True, version=started["version"]
    )
    assert step["step_number"] == 2 and step["state"] == "active"
    done = await tech_help.answer(
        org, person.id, started["id"], worked=True, version=step["version"]
    )
    assert done["state"] == "done" and "Well done" in done["note"]


@pytest.mark.asyncio
async def test_no_shows_another_way_then_stops_and_tells_only_who_shares(amma):
    person, priya, ravi, _, org = amma
    s = (await tech_help.start(org, person.id, question="make text bigger"))["session"]
    first_say = s["say"]
    other = await tech_help.answer(
        org, person.id, s["id"], worked=False, version=s["version"]
    )
    assert other["is_alternative"] is True and other["say"] != first_say
    assert other["step_number"] == 1 and other["note"] == "Let us try another way."
    stuck = await tech_help.answer(
        org, person.id, s["id"], worked=False, version=other["version"]
    )
    assert stuck["state"] == "stuck"
    assert stuck["family_told"] == ["Priya"]
    assert "let Priya know" in stuck["note"]
    priya_view = (await circle.family_view(priya.id))[0]
    assert priya_view["alerts"][0]["title"] == (
        "Amma would like a hand with: Make the writing on my phone bigger (step 1)."
    )
    assert (await circle.family_view(ravi.id))[0]["alerts"] == []


@pytest.mark.asyncio
async def test_a_double_tap_answers_once(amma):
    person, *_, org = amma
    s = (await tech_help.start(org, person.id, guide="torch"))["session"]
    await tech_help.answer(org, person.id, s["id"], worked=True, version=s["version"])
    with pytest.raises(tech_help.StaleAnswer):
        await tech_help.answer(
            org, person.id, s["id"], worked=True, version=s["version"]
        )
    assert (await tech_help.get(org, person.id, s["id"]))["step_number"] == 2


@pytest.mark.asyncio
async def test_a_session_is_its_persons_own(amma):
    person, _, _, stranger, org = amma
    s = (await tech_help.start(org, person.id, guide="torch"))["session"]
    with pytest.raises(NotFound):
        await tech_help.get(org, stranger.id, s["id"])
    async with cs.client(stranger.id, org) as c:
        r = await c.post(
            f"/api/v1/care/help/sessions/{s['id']}/answer",
            json={"worked": True, "version": 0},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_over_http(amma):
    person, *_, org = amma
    async with cs.client(person.id, org) as c:
        topics = (await c.get("/api/v1/care/help/topics")).json()["topics"]
        assert any(t["slug"] == "wifi" for t in topics)
        started = (
            await c.post(
                "/api/v1/care/help/sessions", json={"question": "connect wifi"}
            )
        ).json()
        assert started["matched"] and started["session"]["guide"] == "wifi"
        sid, v = started["session"]["id"], started["session"]["version"]
        nxt = await c.post(
            f"/api/v1/care/help/sessions/{sid}/answer",
            json={"worked": True, "version": v},
        )
        assert nxt.status_code == 200 and nxt.json()["step_number"] == 2
        again = await c.post(
            f"/api/v1/care/help/sessions/{sid}/answer",
            json={"worked": True, "version": v},
        )
        assert again.status_code == 409
