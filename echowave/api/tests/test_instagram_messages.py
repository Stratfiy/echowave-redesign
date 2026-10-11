"""Instagram messages, answered by an agent.

Real: the signed sign-in state, the token exchange's requests, the sealed
token in the database, the webhook signature, the parsing of Meta's body,
the conversation run and its text session, the dedupe in Redis, the card
and its undo, the routes. Stubbed at the boundaries: Instagram's servers
(an httpx MockTransport that records what was sent), the agent's model
turn, and the money (quota, credit and the charge).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from api import constants
from api.db import db_client
from api.enums import AgentEventKind
from api.services.configuration import organization_credentials
from api.services.messaging import instagram
from api.services.workflow import actions, decibyl
from api.tests.support.voice import all_on, clean, make_people

DEFINITION = {
    "nodes": [
        {
            "id": "s",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {"name": "Answer", "prompt": "Answer customers on Instagram."},
        }
    ],
    "edges": [],
}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(constants, "INSTAGRAM_APP_ID", "1234")
    monkeypatch.setattr(constants, "INSTAGRAM_APP_SECRET", "app-secret")
    monkeypatch.setattr(constants, "INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "verify-me")
    monkeypatch.setattr(constants, "INSTAGRAM_CHANNEL_ENABLED", True)
    monkeypatch.setattr(
        organization_credentials,
        "PLATFORM_CREDENTIAL_SECRET",
        Fernet.generate_key().decode(),
    )


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(constants, "INSTAGRAM_APP_ID", "")
    monkeypatch.setattr(constants, "INSTAGRAM_APP_SECRET", "")


@pytest.fixture
async def people(test_engine, monkeypatch, configured):
    all_on(monkeypatch)
    p = await make_people(f"insta-{uuid4().hex[:6]}")
    p.agent = await db_client.create_workflow(
        name="Insta desk",
        workflow_definition=DEFINITION,
        user_id=p.a.id,
        organization_id=p.org,
    )
    yield p
    await clean(p)


class Meta:
    """Instagram's servers, as far as these tests need them."""

    def __init__(self, ig_user_id: str = "17841400000000001"):
        self.ig_user_id = ig_user_id
        self.sent: list[dict] = []
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {request.url.host}{path}")
        if request.url.host == "api.instagram.com" and path == "/oauth/access_token":
            return httpx.Response(200, json={"access_token": "short", "user_id": 1})
        if path == "/access_token":
            return httpx.Response(
                200, json={"access_token": "long", "expires_in": 5183944}
            )
        if path.endswith("/me") and request.method == "GET":
            return httpx.Response(
                200, json={"user_id": self.ig_user_id, "username": "lotusdental"}
            )
        if path.endswith("/me/subscribed_apps"):
            return httpx.Response(200, json={"success": True})
        if path.endswith("/me/messages"):
            self.sent.append(
                {
                    "auth": request.headers.get("authorization"),
                    **json.loads(request.content),
                }
            )
            return httpx.Response(200, json={"message_id": f"m{len(self.sent)}"})
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


class TestTheSignIn:
    def test_the_state_names_the_workspace_and_cannot_be_forged(self, configured):
        state = instagram.make_state(7, 3)
        assert instagram.read_state(state) == (7, 3)
        encoded, signature = state.split(".")
        with pytest.raises(instagram.InstagramError, match="not one of ours"):
            instagram.read_state(f"{encoded}.{'0' * len(signature)}")
        with pytest.raises(instagram.InstagramError, match="expired"):
            instagram.read_state(instagram.make_state(7, 3, now=time.time() - 3600))

    def test_the_link_asks_for_messages_and_comes_back_to_us(self, configured):
        url = instagram.authorize_url(7, 3)
        assert url.startswith("https://www.instagram.com/oauth/authorize?")
        assert "instagram_business_manage_messages" in url
        assert "public%2Finstagram%2Fcallback" in url

    def test_not_set_up_says_so(self, unconfigured):
        with pytest.raises(instagram.InstagramError, match="not set up"):
            instagram.authorize_url(7, 3)

    @pytest.mark.asyncio
    async def test_signing_in_stores_the_account_with_its_token_sealed(self, people):
        meta = Meta()
        account = await instagram.complete_login(
            code="code-1",
            state=instagram.make_state(people.org, people.a.id),
            client=meta.client(),
        )
        assert account.username == "lotusdental"
        assert account.organization_id == people.org
        assert account.encrypted_token != "long"
        assert organization_credentials.unseal(account.encrypted_token) == "long"
        assert any(c.endswith("/me/subscribed_apps") for c in meta.calls)
        stored = await db_client.get_instagram_account(people.org)
        assert stored.ig_user_id == meta.ig_user_id


class TestTheWebhook:
    def test_only_meta_may_post(self, configured):
        body = b'{"object":"instagram"}'
        good = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
        assert instagram.verify_signature(body, good)
        assert not instagram.verify_signature(body, "sha256=" + "0" * 64)
        assert not instagram.verify_signature(body, None)

    def test_registration_answers_our_token_only(self, configured):
        assert instagram.challenge_for("subscribe", "verify-me", "42") == "42"
        assert instagram.challenge_for("subscribe", "wrong", "42") is None

    def test_customers_text_is_kept_and_our_echoes_are_not(self):
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "IG1",
                    "messaging": [
                        {
                            "sender": {"id": "C1"},
                            "recipient": {"id": "IG1"},
                            "message": {"mid": "m1", "text": "Are you open Sunday?"},
                        },
                        {
                            "sender": {"id": "IG1"},
                            "recipient": {"id": "C1"},
                            "message": {"mid": "m2", "text": "Yes", "is_echo": True},
                        },
                        {
                            "sender": {"id": "C1"},
                            "recipient": {"id": "IG1"},
                            "message": {"mid": "m3", "attachments": [{}]},
                        },
                        {"sender": {"id": "C1"}, "read": {"mid": "m1"}},
                    ],
                }
            ],
        }
        assert instagram.parse(payload) == [
            instagram.Incoming("IG1", "C1", "m1", "Are you open Sunday?")
        ]
        assert instagram.parse({"object": "page", "entry": []}) == []

    def test_a_long_reply_is_split_where_instagram_allows(self):
        text = ("word " * 450).strip()
        pieces = instagram.chunks(text)
        assert all(len(p) <= instagram.MAX_MESSAGE_CHARS for p in pieces)
        assert " ".join(pieces) == text


def _turn(text: str):
    return SimpleNamespace(
        session_data={"turns": [{"id": "t1", "assistant_message": {"text": text}}]}
    )


@pytest.mark.asyncio
class TestAnswering:
    async def _connected(self, people, meta, *, with_agent=True):
        account = await instagram.complete_login(
            code="c",
            state=instagram.make_state(people.org, people.a.id),
            client=meta.client(),
        )
        if with_agent:
            await db_client.set_instagram_agent(
                organization_id=people.org,
                account_id=account.id,
                workflow_id=people.agent.id,
            )
        return account

    def _money(self):
        return (
            patch(
                "api.services.quota_service.authorize_workflow_run_start",
                AsyncMock(return_value=SimpleNamespace(has_quota=True)),
            ),
            patch(
                "api.services.billing.reservations.has_credit",
                AsyncMock(return_value=True),
            ),
            patch(
                "api.services.billing.events.charge_in_own_session",
                AsyncMock(return_value=0),
            ),
        )

    async def test_the_agent_answers_in_the_same_conversation(self, people):
        meta = Meta(ig_user_id=f"IG{uuid4().hex[:10]}")
        await self._connected(people, meta)
        turn = AsyncMock(return_value=_turn("We are open 9 to 6 on Sunday."))
        quota, credit, charge = self._money()
        with (
            quota,
            credit,
            charge as charged,
            patch(
                "api.services.workflow.text_chat_session_service.execute_pending_text_chat_turn",
                turn,
            ),
        ):
            first = instagram.Incoming(
                meta.ig_user_id, "C1", f"m{uuid4().hex}", "Open Sunday?"
            )
            assert await instagram.handle(first, client=meta.client()) == "answered"
            second = instagram.Incoming(
                meta.ig_user_id, "C1", f"m{uuid4().hex}", "And Monday?"
            )
            assert await instagram.handle(second, client=meta.client()) == "answered"
            # The same message delivered twice is answered once.
            assert await instagram.handle(first, client=meta.client()) == "duplicate"
        assert [m["recipient"]["id"] for m in meta.sent] == ["C1", "C1"]
        assert meta.sent[0]["message"]["text"] == "We are open 9 to 6 on Sunday."
        assert meta.sent[0]["auth"] == "Bearer long"
        # One run for the customer: the second message continued it.
        runs = {call.kwargs["run_id"] for call in turn.await_args_list}
        assert len(runs) == 1
        assert charged.await_count == 2
        run = await db_client.get_workflow_run(runs.pop(), organization_id=people.org)
        assert run.initial_context["channel"] == "instagram"
        assert run.initial_context["instagram_user_id"] == "C1"

    async def test_no_agent_tells_the_owner_once_and_sends_nothing(self, people):
        meta = Meta(ig_user_id=f"IG{uuid4().hex[:10]}")
        await self._connected(people, meta, with_agent=False)
        record = AsyncMock()
        with patch("api.services.workflow.agent_timeline.record", record):
            for _ in range(2):
                incoming = instagram.Incoming(
                    meta.ig_user_id, "C2", f"m{uuid4().hex}", "Hello?"
                )
                assert (
                    await instagram.handle(incoming, client=meta.client()) == "no_agent"
                )
        assert record.await_count == 1
        assert "no agent" in record.await_args.kwargs["summary"]
        assert meta.sent == []

    async def test_no_credit_sends_nothing(self, people):
        meta = Meta(ig_user_id=f"IG{uuid4().hex[:10]}")
        await self._connected(people, meta)
        with patch(
            "api.services.quota_service.authorize_workflow_run_start",
            AsyncMock(
                return_value=SimpleNamespace(
                    has_quota=False, error_code="x", error_message=""
                )
            ),
        ):
            incoming = instagram.Incoming(
                meta.ig_user_id, "C3", f"m{uuid4().hex}", "Hi"
            )
            assert await instagram.handle(incoming, client=meta.client()) == "no_credit"
        assert meta.sent == []

    async def test_one_customer_cannot_spend_the_balance(self, people, monkeypatch):
        monkeypatch.setattr(instagram, "PER_SENDER_PER_HOUR", 1)
        meta = Meta(ig_user_id=f"IG{uuid4().hex[:10]}")
        await self._connected(people, meta)
        turn = AsyncMock(return_value=_turn("Hello!"))
        sender = f"C{uuid4().hex[:8]}"
        quota, credit, charge = self._money()
        with (
            quota,
            credit,
            charge,
            patch(
                "api.services.workflow.text_chat_session_service.execute_pending_text_chat_turn",
                turn,
            ),
        ):
            first = instagram.Incoming(meta.ig_user_id, sender, f"m{uuid4().hex}", "Hi")
            again = instagram.Incoming(meta.ig_user_id, sender, f"m{uuid4().hex}", "Hi")
            assert await instagram.handle(first, client=meta.client()) == "answered"
            assert await instagram.handle(again, client=meta.client()) == "rate_limited"
        assert len(meta.sent) == 1

    async def test_a_message_to_an_account_nobody_connected_is_dropped(self, people):
        incoming = instagram.Incoming("IG-nobody", "C4", f"m{uuid4().hex}", "Hi")
        assert await instagram.handle(incoming) == "unknown_account"


@pytest.mark.asyncio
class TestFromChat:
    async def test_not_set_up_is_said_without_a_button(self, people, unconfigured):
        result = await instagram.for_thread(people.org, {})
        assert result["status"] == "unavailable"

    async def test_not_connected_puts_the_button_on_the_thread(self, people):
        record = AsyncMock()
        with patch("api.services.workflow.agent_timeline.record", record):
            result = await instagram.for_thread(people.org, {"why": "Answer DMs"})
        assert result["status"] == "offered"
        assert (
            record.await_args.kwargs["kind"]
            == AgentEventKind.INSTAGRAM_CONNECT_OFFERED.value
        )
        assert (
            AgentEventKind.INSTAGRAM_CONNECT_OFFERED.value
            in decibyl.thread_filter(people.org)["kinds"]
        )

    async def test_connected_the_card_sets_the_agent_and_undo_clears_it(self, people):
        meta = Meta(ig_user_id=f"IG{uuid4().hex[:10]}")
        account = await instagram.complete_login(
            code="c",
            state=instagram.make_state(people.org, people.a.id),
            client=meta.client(),
        )
        said = await instagram.for_thread(people.org, {})
        assert said["status"] == "connected" and said["answered_by"] is None
        card = await actions.resolve(
            organization_id=people.org,
            workflow_id=None,
            arguments={"agent": "Insta desk", "action": actions.ANSWER_INSTAGRAM},
        )
        assert "Account: @lotusdental" in card["preview"]
        assert "Answered by: Insta desk" in card["preview"]
        assert "Never writes first" in card["preview"]
        await actions._execute(people.org, card)
        assert (
            await db_client.get_instagram_account(people.org)
        ).workflow_id == people.agent.id
        await actions._reverse(people.org, card)
        assert (await db_client.get_instagram_account(people.org)).workflow_id is None
        assert account.id == (await db_client.get_instagram_account(people.org)).id

    async def test_an_agent_from_another_workspace_is_refused(self, people):
        meta = Meta(ig_user_id=f"IG{uuid4().hex[:10]}")
        await instagram.complete_login(
            code="c",
            state=instagram.make_state(people.org, people.a.id),
            client=meta.client(),
        )
        with pytest.raises(actions.ActionError, match="No agent"):
            await actions.resolve(
                organization_id=people.org,
                workflow_id=None,
                arguments={
                    "agent": "Somebody else",
                    "action": actions.ANSWER_INSTAGRAM,
                },
            )


class TestDecibylHasIt:
    def test_offered_and_in_the_rules(self, configured):
        assert instagram.TOOL_NAME in {t["name"] for t in decibyl.office_tools(7)}
        assert instagram.TOOL_NAME in decibyl.SYSTEM

    def test_the_kill_switch_takes_it_away(self, configured, monkeypatch):
        monkeypatch.setattr(constants, "INSTAGRAM_CHANNEL_ENABLED", False)
        assert instagram.TOOL_NAME not in {t["name"] for t in decibyl.office_tools(7)}

    def test_a_message_about_instagram_points_at_it(self):
        from api.services.routing import tool_intents

        pattern, names = tool_intents.INTENTS["instagram"]
        assert "connect_instagram" in names
        assert pattern.search("can my agent answer our insta DMs")


class TestTheRoutes:
    def test_the_webhook_refuses_a_bad_signature_and_queues_a_good_one(
        self, configured
    ):
        from api.app import app

        body = json.dumps(
            {
                "object": "instagram",
                "entry": [
                    {
                        "id": "IG9",
                        "messaging": [
                            {
                                "sender": {"id": "C9"},
                                "recipient": {"id": "IG9"},
                                "message": {"mid": "m9", "text": "Hi"},
                            }
                        ],
                    }
                ],
            }
        ).encode()
        signature = (
            "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
        )
        queued = AsyncMock()
        client = TestClient(app)
        with patch("api.routes.instagram.enqueue_job", queued):
            bad = client.post(
                "/api/v1/public/instagram/webhook",
                content=body,
                headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
            )
            good = client.post(
                "/api/v1/public/instagram/webhook",
                content=body,
                headers={
                    "X-Hub-Signature-256": signature,
                    "Content-Type": "application/json",
                },
            )
        assert bad.status_code == 403
        assert good.status_code == 200 and good.json()["queued"] == 1
        assert queued.await_args.args[1:] == ("IG9", "C9", "m9", "Hi")

    def test_registration(self, configured):
        from api.app import app

        client = TestClient(app)
        ok = client.get(
            "/api/v1/public/instagram/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "77",
            },
        )
        assert ok.status_code == 200 and ok.text == "77"
        no = client.get(
            "/api/v1/public/instagram/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "x",
                "hub.challenge": "77",
            },
        )
        assert no.status_code == 403
