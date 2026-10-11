"""Ask every customer for a review, from Chat -- within Google's rules.

The owner says "ask my customers for Google reviews" and gives the link.
One card adds a Review request message to the agent: after each real
conversation, to everyone (Google forbids asking only the happy ones), with
nothing offered in return, at most once per person every 90 days, never
after a missed call, a voicemail or a test.

Real: the card, the draft it writes, the step's validation, the decision to
send or not, and the once-per-person memory in Redis. Stubbed: the carrier.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from api import constants
from api.db import db_client
from api.services.messaging import follow_up, review_requests
from api.services.workflow import actions
from api.services.workflow.dto import ReactFlowDTO, SmsNodeData
from api.tests.support.voice import all_on, clean, make_people

DEFINITION = {
    "nodes": [
        {
            "id": "s",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {"name": "Answer", "prompt": "Hi"},
        },
        {
            "id": "e",
            "type": "endCall",
            "position": {"x": 0, "y": 200},
            "data": {"name": "Close", "prompt": "Bye"},
        },
    ],
    "edges": [
        {
            "id": "s-e",
            "source": "s",
            "target": "e",
            "data": {"label": "Done", "condition": "Done"},
        }
    ],
}
LINK = "https://g.page/r/CaBcDeFgHiJk/review"
ASK = {
    "agent": "Lotus front desk",
    "review_link": LINK,
    "business_name": "Lotus Dental Care",
    "why": "Asked in the thread",
}
TWILIO = {"account_sid": "AC1", "auth_token": "t", "from_numbers": ["+911111111111"]}


@pytest.fixture
def no_template(monkeypatch):
    monkeypatch.setattr(constants, "WHATSAPP_REVIEW_TEMPLATE", "")


@pytest.fixture
def with_template(monkeypatch):
    monkeypatch.setattr(constants, "WHATSAPP_REVIEW_TEMPLATE", "review_request_v1")


@pytest.fixture
def carrier():
    """A phone carrier on file, so SMS can go."""
    with patch.object(
        db_client,
        "get_default_telephony_configuration",
        AsyncMock(return_value=SimpleNamespace(provider="twilio")),
    ):
        yield


@pytest.fixture
async def people(test_engine, monkeypatch):
    all_on(monkeypatch)
    p = await make_people(f"reviews-{uuid4().hex[:6]}")
    p.agent = await db_client.create_workflow(
        name="Lotus front desk",
        workflow_definition=DEFINITION,
        user_id=p.a.id,
        organization_id=p.org,
    )
    yield p
    await clean(p)


async def _card(people, arguments=ASK) -> dict:
    return await actions.resolve(
        organization_id=people.org,
        workflow_id=None,
        arguments={**arguments, "action": actions.ASK_FOR_REVIEWS},
    )


class TestGooglesRules:
    @pytest.mark.parametrize(
        "words",
        [
            "Review us and get 10% off your next visit: {link}",
            "Leave a review for a free cleaning! {link}",
            "Reviews earn cashback: {link}",
            "Please give us 5 stars: {link}",
            "If you were happy with us, please review: {link}",
        ],
    )
    def test_a_reward_or_a_gate_is_turned_back(self, words):
        with pytest.raises(review_requests.ReviewSetupError, match="Google"):
            review_requests.check_words(words)

    def test_an_honest_ask_passes_and_feel_free_is_not_a_reward(self):
        review_requests.check_words(
            "Thanks for visiting! Feel free to tell us how we did: {link}"
        )

    def test_the_business_name_is_not_read_as_an_offer(self):
        text = review_requests.message_for("Gift Corner", None)
        assert "Gift Corner" in text and "{link}" in text

    def test_own_words_must_carry_the_link(self):
        with pytest.raises(review_requests.ReviewSetupError, match="link"):
            review_requests.message_for("Lotus", "Please review us!")


class TestTheLink:
    @pytest.mark.parametrize(
        ("link", "platform"),
        [
            (LINK, "Google"),
            ("https://search.google.com/local/writereview?placeid=ChIJ1", "Google"),
            ("https://www.justdial.com/Pune/Lotus-Dental/review", "Justdial"),
            ("https://www.practo.com/pune/clinic/lotus/reviews", "Practo"),
        ],
    )
    def test_the_card_names_the_platform(self, link, platform):
        assert review_requests.platform_of(review_requests.check_link(link)) == platform

    def test_a_bare_address_gets_https(self):
        assert review_requests.check_link("g.page/r/Ca/review").startswith("https://")

    @pytest.mark.parametrize("link", ["", "http://127.0.0.1/review", "not a link"])
    def test_a_link_that_is_not_a_review_page_is_refused(self, link):
        with pytest.raises(review_requests.ReviewSetupError):
            review_requests.check_link(link)


class TestTheChannel:
    def test_whatsapp_on_the_approved_template(self, with_template):
        data = review_requests.node_data(
            link=LINK, business="Lotus, Pune", text="Thanks {link}", channel="whatsapp"
        )
        assert data["template_name"] == "review_request_v1"
        # The comma in the name must not split it into two template values.
        assert data["template_params"] == f"Lotus  Pune,{LINK}"
        assert review_requests.channel_for(None) == ("whatsapp", None)

    def test_without_a_template_it_is_sms_and_the_card_says_so(self, no_template):
        channel, note = review_requests.channel_for(None)
        assert channel == "sms" and "WhatsApp follows" in note

    def test_whatsapp_asked_for_without_a_template_is_explained(self, no_template):
        with pytest.raises(review_requests.ReviewSetupError, match="SMS"):
            review_requests.channel_for("whatsapp")


@pytest.mark.asyncio
class TestTheCard:
    async def test_it_shows_who_what_and_how_often(self, people, no_template, carrier):
        card = await _card(people)
        preview = card["preview"]
        assert "Agent: Lotus front desk" in preview
        assert f"Review page: Google ({LINK})" in preview
        assert "Thank you for choosing Lotus Dental Care" in preview
        assert "everyone the agent really spoke with" in preview
        assert "once per person every 90 days" in preview
        assert "By SMS" in preview
        node = card["args"]["node"]
        # Asked of everyone: no condition on a rating or an outcome.
        assert not node.get("send_when_variable")
        assert node["only_if_answered"] is True
        assert node["once_per_contact_days"] == 90
        assert card["state"] == actions.PROPOSED

    async def test_a_reward_never_reaches_a_card(self, people, no_template, carrier):
        with pytest.raises(actions.ActionError, match="Google"):
            await _card(people, {**ASK, "message": "20% off for a review: {link}"})

    async def test_an_agent_from_nowhere_is_refused(self, people, no_template, carrier):
        with pytest.raises(actions.ActionError, match="No agent"):
            await _card(people, {**ASK, "agent": "Somebody else's bot"})

    async def test_no_carrier_and_no_template_is_said_not_carded(
        self, people, no_template
    ):
        with pytest.raises(actions.ActionError, match="no.*carrier|none yet"):
            await _card(people)

    async def test_on_the_whatsapp_template_no_carrier_is_needed(
        self, people, with_template
    ):
        card = await _card(people)
        assert "Message (WhatsApp)" in card["preview"]
        assert card["args"]["node"]["template_name"] == "review_request_v1"

    async def test_confirm_puts_one_step_in_the_draft_and_again_replaces_it(
        self, people, no_template, carrier
    ):
        card = await _card(people)
        said = await actions._execute(people.org, card)
        assert "Publish" in said
        draft = await db_client.get_draft_version(people.agent.id)
        steps = [n for n in draft.workflow_json["nodes"] if n["type"] == "sms"]
        assert len(steps) == 1
        assert steps[0]["data"]["name"] == "Review request"
        assert LINK in steps[0]["data"]["body"]
        # The draft still opens in the editor with the step in it.
        ReactFlowDTO.model_validate(draft.workflow_json)

        card = await _card(
            people, {**ASK, "review_link": "https://g.page/r/New/review"}
        )
        await actions._execute(people.org, card)
        draft = await db_client.get_draft_version(people.agent.id)
        steps = [n for n in draft.workflow_json["nodes"] if n["type"] == "sms"]
        assert len(steps) == 1 and "New/review" in steps[0]["data"]["body"]
        # The agent's own steps are untouched.
        assert {n["id"] for n in draft.workflow_json["nodes"]} >= {"s", "e"}


class TestTheStep:
    def test_a_window_outside_a_year_is_refused(self):
        with pytest.raises(ValidationError, match="365"):
            SmsNodeData(name="Review", body="x", once_per_contact_days=0)
        with pytest.raises(ValidationError, match="365"):
            SmsNodeData(name="Review", body="x", once_per_contact_days=400)

    def test_old_steps_are_unchanged(self):
        node = SmsNodeData(name="Confirm", body="Done.")
        assert node.only_if_answered is False and node.once_per_contact_days is None


def _run(**kw):
    base = {
        "mode": "twilio",
        "annotations": {},
        "gathered_context": {},
        "answered_at": datetime.now(UTC) - timedelta(minutes=3),
        "billable_seconds": 120,
        "ended_at": datetime.now(UTC),
    }
    return SimpleNamespace(**{**base, **kw})


class TestARealConversation:
    def test_an_answered_call_is_one(self):
        assert follow_up.real_conversation(_run())

    def test_a_missed_call_is_not(self):
        assert not follow_up.real_conversation(
            _run(answered_at=None, billable_seconds=0)
        )

    @pytest.mark.parametrize("ended", ["voicemail", "no_answer", "busy"])
    def test_a_voicemail_or_nobody_is_not(self, ended):
        assert not follow_up.real_conversation(
            _run(gathered_context={"call_disposition": ended})
        )

    def test_a_test_call_is_not(self):
        assert not follow_up.real_conversation(_run(annotations={"tester": True}))
        assert not follow_up.real_conversation(_run(mode="webrtc"))

    def test_a_chat_is(self):
        assert follow_up.real_conversation(
            _run(mode="textchat", answered_at=None, billable_seconds=None)
        )


def _response():
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"sid": "SM1"}
    response.text = '{"sid": "SM1"}'
    return response


@pytest.mark.asyncio
class TestSending:
    async def _deliver(self, node, *, answered, to, org):
        post = AsyncMock(return_value=_response())
        with patch("httpx.AsyncClient.post", post):
            result = await follow_up.deliver(
                node,
                variables={"phone_number": to},
                provider="twilio",
                credentials=TWILIO,
                default_from="+911111111111",
                answered=answered,
                organization_id=org,
                workflow_id=7,
            )
        return result, post

    async def test_not_after_a_missed_call(self):
        node = SmsNodeData(name="Review request", body="Thanks", only_if_answered=True)
        result, post = await self._deliver(
            node, answered=False, to="+919876543210", org=1
        )
        assert result is None and post.await_count == 0
        result, post = await self._deliver(
            node, answered=None, to="+919876543210", org=1
        )
        assert result is None and post.await_count == 0

    async def test_once_per_person_then_not_again(self):
        org = int(uuid4().int % 1_000_000) + 10_000
        node = SmsNodeData(
            name="Review request",
            body="Thanks",
            only_if_answered=True,
            once_per_contact_days=90,
        )
        first, post = await self._deliver(
            node, answered=True, to="+919876543210", org=org
        )
        assert first is not None and first.ok and post.await_count == 1
        again, post = await self._deliver(
            node, answered=True, to="+91 98765 43210", org=org
        )
        assert again is None and post.await_count == 0
        # Somebody else is still asked.
        other, post = await self._deliver(
            node, answered=True, to="+919812345678", org=org
        )
        assert other is not None and post.await_count == 1

    async def test_a_step_without_the_options_sends_as_before(self):
        node = SmsNodeData(name="Confirm", body="Done.")
        result, post = await self._deliver(
            node, answered=None, to="+919876543210", org=1
        )
        assert result is not None and post.await_count == 1


class TestDecibylHasIt:
    def test_offered_with_its_rule(self, monkeypatch):
        from api.services.workflow import decibyl

        names = {t["name"] for t in decibyl.office_tools(7)}
        assert review_requests.TOOL_NAME in names
        assert review_requests.TOOL_NAME in decibyl.system_prompt(7)

    def test_the_kill_switch_takes_it_away(self, monkeypatch):
        from api.services.workflow import decibyl

        monkeypatch.setattr(constants, "REVIEW_REQUESTS_ENABLED", False)
        assert review_requests.TOOL_NAME not in {
            t["name"] for t in decibyl.office_tools(7)
        }

    def test_a_message_about_reviews_points_at_it(self):
        from api.services.routing import tool_intents

        pattern, names = tool_intents.INTENTS["reviews"]
        assert "ask_for_reviews" in names
        assert pattern.search("can you get us more google reviews")
