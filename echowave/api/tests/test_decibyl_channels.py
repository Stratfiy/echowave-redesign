"""Decibyl in your apps (DCH-1, KAN-277): proof, parsing, linking, cards.

What matters most, in order:
1. Nothing is acted on unless the platform proved it sent it (Slack's HMAC
   and timestamp, Teams' Bot Framework JWT bound to the activity's
   serviceUrl, Telegram's secret header).
2. A message from somebody nobody linked gets "link me first" and nothing
   else; a code links exactly one member, once.
3. A button press settles a card as the linked member, in their own
   organisation only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.services.messaging.channels import (
    base,
    dispatch,
    identities,
    slack,
    teams,
    telegram,
)
from api.services.messaging.channels.base import Card, Inbound, Tap

# ── buttons ───────────────────────────────────────────────────────────────────


def test_a_button_id_round_trips_and_rejects_anything_else():
    assert base.parse_button_id(base.button_id(42, "confirm")) == Tap(42, "confirm")
    assert base.parse_button_id("card:42:delete-everything") is None
    assert base.parse_button_id("card:x:confirm") is None
    assert base.parse_button_id(None) is None


def test_a_proposed_card_offers_confirm_and_decline_and_a_done_one_nothing():
    assert [v for v, _ in Card(1, "Send invoice").buttons()] == ["confirm", "decline"]
    assert Card(1, "x", state="done").buttons() == []
    assert [v for v, _ in Card(1, "x", state="done", reversible=True).buttons()] == [
        "undo"
    ]


# ── Slack ─────────────────────────────────────────────────────────────────────

SECRET = "slack-signing-secret"


def _sign(body: bytes, ts: int, secret: str = SECRET) -> str:
    return (
        "v0="
        + hmac.new(
            secret.encode(), b"v0:" + str(ts).encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )


@pytest.fixture
def slack_env(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SLACK_CLIENT_ID", "cid")


def test_slack_accepts_its_own_signature_only(slack_env):
    body, ts = b'{"a":1}', int(time.time())
    assert slack.verify(body, str(ts), _sign(body, ts))
    assert not slack.verify(body, str(ts), _sign(body, ts, "other"))
    assert not slack.verify(b'{"a":2}', str(ts), _sign(body, ts))


def test_slack_refuses_a_replayed_request(slack_env):
    body, ts = b"{}", int(time.time()) - 6 * 60
    assert not slack.verify(body, str(ts), _sign(body, ts))


def test_slack_refuses_everything_when_not_set_up(monkeypatch):
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    ts = int(time.time())
    assert not slack.verify(b"{}", str(ts), _sign(b"{}", ts))


def test_slack_reads_a_direct_message_and_ignores_the_rest():
    dm = {
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "message",
            "channel_type": "im",
            "user": "U1",
            "text": "hi",
            "channel": "D1",
        },
    }
    inbound = slack.parse_event(dm)
    assert inbound.external_id == "T1:U1"
    assert inbound.ref == {"team_id": "T1", "channel": "D1"}
    own = {**dm, "event": {**dm["event"], "bot_id": "B1"}}
    public = {**dm, "event": {**dm["event"], "channel_type": "channel"}}
    assert slack.parse_event(own) is None
    assert slack.parse_event(public) is None


def test_slack_reads_a_card_press():
    payload = {
        "type": "block_actions",
        "team": {"id": "T1"},
        "user": {"id": "U1", "name": "meera"},
        "channel": {"id": "D1"},
        "trigger_id": "tr1",
        "actions": [{"value": "card:7:confirm"}],
    }
    inbound = slack.parse_interaction(payload)
    assert inbound.tap == Tap(7, "confirm")
    assert inbound.external_id == "T1:U1"


def _app() -> FastAPI:
    from api.routes.public_decibyl_channels import router

    app = FastAPI()
    app.include_router(router)
    return app


def test_the_slack_endpoint_answers_its_url_check_and_refuses_forgeries(slack_env):
    client = TestClient(_app())
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    ts = int(time.time())
    ok = client.post(
        "/public/slack/events",
        content=body,
        headers={
            "X-Slack-Request-Timestamp": str(ts),
            "X-Slack-Signature": _sign(body, ts),
        },
    )
    assert ok.json() == {"challenge": "abc"}
    forged = client.post(
        "/public/slack/events",
        content=body,
        headers={"X-Slack-Request-Timestamp": str(ts), "X-Slack-Signature": "v0=00"},
    )
    assert forged.status_code == 403


def test_a_slack_message_is_handed_on_after_the_200(slack_env):
    client = TestClient(_app())
    body = json.dumps(
        {
            "team_id": "T1",
            "event_id": f"Ev{uuid.uuid4().hex}",
            "event": {
                "type": "message",
                "channel_type": "im",
                "user": "U1",
                "text": "hi",
                "channel": "D1",
            },
        }
    ).encode()
    ts = int(time.time())
    with (
        patch.object(
            dispatch, "handle", new=AsyncMock(return_value="accepted")
        ) as handle,
        patch(
            "api.services.messaging.whatsapp_inbound.seen_before",
            new=AsyncMock(return_value=False),
        ),
    ):
        response = client.post(
            "/public/slack/events",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": str(ts),
                "X-Slack-Signature": _sign(body, ts),
            },
        )
    assert response.json() == {"status": "accepted"}
    assert handle.await_args.args[0].external_id == "T1:U1"


def test_slack_card_blocks_carry_the_card_id():
    blocks = slack.blocks(Card(9, "Refund ₹500", effect="Refunds order 12"))
    values = [e["value"] for e in blocks[1]["elements"]]
    assert values == ["card:9:confirm", "card:9:decline"]


# ── Teams ─────────────────────────────────────────────────────────────────────

APP_ID = "00000000-0000-0000-0000-000000000abc"
SERVICE_URL = "https://smba.trafficmanager.net/in/"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def teams_env(monkeypatch, rsa_key):
    monkeypatch.setenv("MICROSOFT_APP_ID", APP_ID)
    monkeypatch.setenv("MICROSOFT_APP_PASSWORD", "pw")

    class Key:
        key = rsa_key.public_key()

    class Keys:
        def get_signing_key_from_jwt(self, _token):
            return Key()

    monkeypatch.setattr(teams, "_jwks", lambda: Keys())


def _token(rsa_key, **overrides):
    claims = {
        "iss": teams.ISSUER,
        "aud": APP_ID,
        "serviceurl": SERVICE_URL,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        **overrides,
    }
    return "Bearer " + jwt.encode(claims, rsa_key, algorithm="RS256")


def _activity(**overrides):
    return {
        "type": "message",
        "id": "a1",
        "serviceUrl": SERVICE_URL,
        "from": {"id": "29:x", "aadObjectId": "aad-1", "name": "Meera"},
        "conversation": {"id": "c1", "conversationType": "personal", "tenantId": "tn1"},
        "text": "<at>Decibyl</at> what's due today?",
        **overrides,
    }


def test_teams_accepts_a_token_for_this_bot_and_this_service_url(teams_env, rsa_key):
    assert teams.verify(_token(rsa_key), _activity())


def test_teams_refuses_a_token_for_another_bot(teams_env, rsa_key):
    assert not teams.verify(_token(rsa_key, aud="someone-else"), _activity())


def test_teams_refuses_a_token_from_another_issuer(teams_env, rsa_key):
    assert not teams.verify(_token(rsa_key, iss="https://evil.example"), _activity())


def test_teams_refuses_a_service_url_the_token_does_not_vouch_for(teams_env, rsa_key):
    """Otherwise anyone could make the bot post, with our token, to their host."""
    assert not teams.verify(
        _token(rsa_key), _activity(serviceUrl="https://attacker.example/")
    )


def test_teams_refuses_an_expired_token(teams_env, rsa_key):
    stale = _token(rsa_key, exp=datetime.now(UTC) - timedelta(hours=1))
    assert not teams.verify(stale, _activity())


def test_teams_refuses_without_a_token(teams_env):
    assert not teams.verify(None, _activity())


def test_teams_reads_a_personal_message_without_the_mention():
    inbound = teams.parse(_activity())
    assert inbound.external_id == "tn1:aad-1"
    assert inbound.text == "what's due today?"
    assert inbound.ref["conversation_id"] == "c1"


def test_teams_ignores_channels_and_group_chats():
    group = _activity(
        conversation={"id": "c2", "conversationType": "groupChat", "tenantId": "tn1"}
    )
    assert teams.parse(group) is None


def test_teams_reads_a_card_press():
    inbound = teams.parse(_activity(text="", value={"card": "card:5:decline"}))
    assert inbound.tap == Tap(5, "decline")


def test_teams_cards_submit_the_card_id():
    card = teams.adaptive_card(Card(3, "Book a demo"))
    data = [a["data"]["card"] for a in card["content"]["actions"]]
    assert data == ["card:3:confirm", "card:3:decline"]


# ── Telegram ─────────────────────────────────────────────────────────────────


def test_telegram_checks_its_secret(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    assert telegram.verify("s3cret")
    assert not telegram.verify("guess")
    assert not telegram.verify(None)


def test_telegram_ignores_group_chats():
    update = {
        "update_id": 1,
        "message": {"message_id": 1, "chat": {"id": -5, "type": "group"}, "text": "hi"},
    }
    assert telegram.parse(update) is None


# ── linking and cards, end to end through the dispatcher ─────────────────────


class Recorder:
    name = "slack"

    def __init__(self):
        self.texts: list[str] = []
        self.cards: list[Card] = []

    def enabled(self, organization_id=None):
        return True

    async def send_text(self, ref, text):
        self.texts.append(text)
        return True

    async def send_card(self, ref, card):
        self.cards.append(card)
        return True

    async def acknowledge(self, inbound, note=""):
        return None


async def _member(slug: str):
    from api.db import db_client
    from api.db.models import OrganizationModel, UserModel

    async with db_client.async_session() as session:
        org = OrganizationModel(
            provider_id=f"org-{slug}-{uuid.uuid4().hex[:6]}", quota_decibyl_tokens=0
        )
        user = UserModel(provider_id=f"user-{slug}-{uuid.uuid4().hex[:6]}")
        session.add_all([org, user])
        await session.commit()
        return org.id, user.id


@pytest.fixture
def channels_on(db_session):
    with patch.object(dispatch, "channel_on", return_value=True):
        yield


@pytest.fixture
def recorder():
    rec = Recorder()
    with patch.object(dispatch, "adapter_for", return_value=rec):
        yield rec


def _msg(external_id: str, text: str = "", tap: Tap | None = None) -> Inbound:
    return Inbound(
        channel=base.SLACK,
        external_id=external_id,
        message_id=uuid.uuid4().hex,
        text=text,
        tap=tap,
        ref={"team_id": "T1", "channel": "D1"},
    )


@pytest.mark.asyncio
async def test_a_stranger_is_asked_to_link_and_nothing_else(channels_on, recorder):
    with patch("api.services.workflow.decibyl.ask", new=AsyncMock()) as ask:
        status = await dispatch.handle(
            _msg(f"T1:{uuid.uuid4().hex}", "delete my contacts")
        )
    assert status == "unlinked"
    assert recorder.texts == [dispatch.LINK_FIRST]
    ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_code_links_one_member_once(channels_on, recorder):
    org_id, user_id = await _member("link")
    code = await identities.start_link(
        organization_id=org_id, user_id=user_id, channel=base.SLACK
    )
    who = f"T1:{uuid.uuid4().hex}"

    assert await dispatch.handle(_msg(who, code)) == "linked"
    linked = await identities.find(base.SLACK, who)
    assert (linked.organization_id, linked.user_id) == (org_id, user_id)

    # The same code again, from somebody else, links nobody.
    assert await dispatch.handle(_msg(f"T1:{uuid.uuid4().hex}", code)) == "bad_code"


@pytest.mark.asyncio
async def test_a_linked_member_asks_decibyl_as_themselves(channels_on, recorder):
    org_id, user_id = await _member("ask")
    code = await identities.start_link(
        organization_id=org_id, user_id=user_id, channel=base.SLACK
    )
    who = f"T1:{uuid.uuid4().hex}"
    await dispatch.handle(_msg(who, code))

    with patch("api.services.workflow.decibyl.ask", new=AsyncMock()) as ask:
        assert await dispatch.handle(_msg(who, "what's due today?")) == "accepted"
    kwargs = ask.await_args.kwargs
    assert (kwargs["organization_id"], kwargs["user_id"]) == (org_id, user_id)
    assert kwargs["reply_to"]["channel"] == base.SLACK


@pytest.mark.asyncio
async def test_a_press_settles_the_card_as_the_member_in_their_own_organisation(
    channels_on, recorder
):
    org_id, user_id = await _member("tap")
    code = await identities.start_link(
        organization_id=org_id, user_id=user_id, channel=base.SLACK
    )
    who = f"T1:{uuid.uuid4().hex}"
    await dispatch.handle(_msg(who, code))

    with patch(
        "api.services.workflow.actions.settle",
        new=AsyncMock(return_value={"label": "Refund", "state": "armed"}),
    ) as settle:
        assert await dispatch.handle(_msg(who, tap=Tap(11, "confirm"))) == "confirm"
    assert settle.await_args.kwargs == {
        "organization_id": org_id,
        "event_id": 11,
        "verb": "confirm",
        "user_id": user_id,
    }
    assert recorder.cards[-1].state == "armed"


@pytest.mark.asyncio
async def test_a_press_from_a_stranger_settles_nothing(channels_on, recorder):
    with patch("api.services.workflow.actions.settle", new=AsyncMock()) as settle:
        status = await dispatch.handle(
            _msg(f"T1:{uuid.uuid4().hex}", tap=Tap(11, "confirm"))
        )
    assert status == "unlinked"
    settle.assert_not_awaited()


# ── a stranger hears "link me first" (KAN-277 production fix) ─────────────────
#
# In production the feature is on for named organisations only
# (FEATURE_ORG_OVERRIDES) and off globally. Asking ``channel_on`` with no
# organisation said no to every stranger, so nobody unlinked got any reply.


@pytest.fixture
def flags_per_org_only(db_session, monkeypatch):
    """Every channel flag off globally; the returned function names who has
    them on."""
    from api import constants

    for flag in (
        "DECIBYL_CHANNELS_ENABLED",
        "DECIBYL_TELEGRAM_ENABLED",
        "DECIBYL_SLACK_ENABLED",
        "DECIBYL_TEAMS_ENABLED",
    ):
        monkeypatch.setattr(constants, flag, False)

    def set_orgs(*org_ids: int) -> None:
        ids = ",".join(str(i) for i in org_ids) or "0"
        monkeypatch.setattr(
            constants,
            "FEATURE_ORG_OVERRIDES",
            f"decibyl_channels:{ids};decibyl_telegram:{ids};decibyl_slack:{ids}",
        )

    set_orgs()
    return set_orgs


async def _install_slack(team_id: str, organization_id: int) -> None:
    from api.db import db_client
    from api.db.channel_identity_models import SlackInstallationModel

    async with db_client.async_session() as session:
        session.add(
            SlackInstallationModel(
                team_id=team_id,
                organization_id=organization_id,
                team_name="Acme",
                encrypted_bot_token="not-a-real-token",
            )
        )
        await session.commit()


def _team() -> str:
    return f"T{uuid.uuid4().hex[:10].upper()}"


def _slack_msg(team_id: str, text: str) -> Inbound:
    return Inbound(
        channel=base.SLACK,
        external_id=f"{team_id}:{uuid.uuid4().hex[:8]}",
        message_id=uuid.uuid4().hex,
        text=text,
        ref={"team_id": team_id, "channel": "D1"},
    )


@pytest.mark.asyncio
async def test_a_stranger_hears_link_first_when_the_feature_is_on_per_org_only(
    flags_per_org_only, recorder
):
    org_id, _ = await _member("perorg")
    flags_per_org_only(org_id)
    inbound = Inbound(
        channel=base.TELEGRAM,
        external_id=uuid.uuid4().hex,
        message_id=uuid.uuid4().hex,
        text="hello",
        ref={"chat_id": "42"},
    )
    assert dispatch.channel_on(base.TELEGRAM) is False  # the old gate said no
    with patch("api.services.workflow.decibyl.ask", new=AsyncMock()) as ask:
        assert await dispatch.handle(inbound) == "unlinked"
    assert recorder.texts == [dispatch.LINK_FIRST]
    ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stranger_in_the_slack_of_a_switched_on_org_hears_link_first(
    flags_per_org_only, recorder
):
    org_id, _ = await _member("slackon")
    flags_per_org_only(org_id)
    team = _team()
    await _install_slack(team, org_id)

    assert await dispatch.handle(_slack_msg(team, "hi")) == "unlinked"
    assert recorder.texts == [dispatch.LINK_FIRST]


@pytest.mark.asyncio
async def test_the_slack_of_a_switched_off_org_stays_quiet(
    flags_per_org_only, recorder
):
    org_id, _ = await _member("slackoff")
    other_id, _ = await _member("slackother")
    flags_per_org_only(other_id)
    team = _team()
    await _install_slack(team, org_id)

    assert await dispatch.handle(_slack_msg(team, "hi")) == "unlinked"
    assert recorder.texts == []


@pytest.mark.asyncio
async def test_a_stranger_gets_nothing_when_the_app_is_not_set_up(
    flags_per_org_only, recorder, monkeypatch
):
    monkeypatch.setattr(recorder, "enabled", lambda organization_id=None: False)
    assert await dispatch.handle(_msg(f"T1:{uuid.uuid4().hex}", "hi")) == "unlinked"
    assert recorder.texts == []


# ── Settings: Slack setup state and the exact redirect URL ────────────────────


def test_the_slack_redirect_uri_is_on_the_api_host(monkeypatch):
    from api import constants
    from api.routes import public_decibyl_channels

    monkeypatch.setattr(constants, "BACKEND_API_ENDPOINT", "https://api.decibyl.ai/")
    expected = "https://api.decibyl.ai/api/v1/public/slack/oauth/callback"
    assert slack.redirect_uri() == expected
    # The install link and the code exchange both use this one value.
    assert public_decibyl_channels.slack_redirect_uri() == expected


@pytest.mark.asyncio
async def test_slack_setup_says_whether_slack_was_added_and_shows_admins_the_url(
    db_session,
):
    from types import SimpleNamespace

    from api.db import db_client
    from api.db.models import OrganizationMembershipModel
    from api.routes import channel_links

    org_id, admin_id = await _member("setup")
    _, member_id = await _member("setupmember")
    async with db_client.async_session() as session:
        session.add_all(
            [
                OrganizationMembershipModel(
                    organization_id=org_id, user_id=admin_id, role="admin"
                ),
                OrganizationMembershipModel(
                    organization_id=org_id, user_id=member_id, role="member"
                ),
            ]
        )
        await session.commit()

    before = await channel_links._slack_setup(SimpleNamespace(id=admin_id), org_id)
    assert before["installed"] is False
    assert before["can_install"] is True
    assert before["redirect_uri"].endswith("/api/v1/public/slack/oauth/callback")

    await _install_slack(_team(), org_id)
    after = await channel_links._slack_setup(SimpleNamespace(id=member_id), org_id)
    assert after == {
        "installed": True,
        "workspace": "Acme",
        "can_install": False,
        "redirect_uri": None,
    }
