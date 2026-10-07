"""A person's Decibyl email address (screen 23; launch stream identity).

Done when: a name is checked server-side with specific reasons that name
nobody; two requests for one name cannot both win; the address is active
only after a verified delivery; inbound is signed, deduplicated, sized,
threaded and routed to its owner, and mail for nobody is quarantined;
sending is an action card from the owner's own address only, runs once,
and a lost answer is outcome unknown; bounces and complaints change the
state; and the virtual card is interest only.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from email.message import EmailMessage
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api import constants
from api.db import db_client
from api.db.identity_models import EmailIdentityMessageModel, EmailIdentitySendModel
from api.services.identity import cards, email_identity
from api.services.workflow import actions
from api.tests.identity_support import client_as, flags, no_queue, team  # noqa: F401

SECRET = "inbound-secret"


@pytest.fixture
def on(monkeypatch):
    flags(monkeypatch, "IDENTITY_EMAIL_ENABLED", "TASK_LEDGER_ENABLED")
    monkeypatch.setattr(constants, "EMAIL_IDENTITY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(constants, "EMAIL_IDENTITY_DOMAIN", "decibyl.test")


@pytest.fixture
def mail(monkeypatch):
    """The mail server: send_email for the check message, SMTP for sends."""
    sent = AsyncMock(return_value=type("R", (), {"ok": True, "error": None})())
    monkeypatch.setattr("api.services.messaging.email.send_email", sent)
    monkeypatch.setattr(
        "api.services.messaging.email.email_is_configured", lambda: True
    )
    return sent


def _name(team, suffix=""):
    return f"meera{team.member.id}{suffix}"


def _mime(
    to: str,
    subject: str,
    *,
    message_id: str,
    references: str | None = None,
    attach: str | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = "Ravi <ravi@example.com>"
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if references:
        message["References"] = references
        message["In-Reply-To"] = references
    message.set_content("Hello Meera, the invoice is attached.")
    if attach:
        message.add_attachment(
            b"MZ\x90", maintype="application", subtype="octet-stream", filename=attach
        )
    return message.as_bytes()


def _signed(body: dict) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return raw, {
        "X-Decibyl-Signature": f"sha256={sig}",
        "X-Decibyl-Timestamp": ts,
        "Content-Type": "application/json",
    }


async def _activate(team, mail) -> str:
    alias = _name(team)
    await email_identity.reserve(team.member.id, team.org, alias)
    await email_identity.provision(team.member.id)
    subject = mail.await_args.kwargs["subject"]
    status = await email_identity.receive(
        f"{alias}@decibyl.test",
        _mime(f"{alias}@decibyl.test", subject, message_id=f"<probe-{alias}@x>"),
    )
    assert status == "activated"
    return alias


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_routes_are_not_there(self, team):
        async with client_as(team.as_user(team.member)) as c:
            assert (await c.get("/api/v1/me/email-identity")).status_code == 404
            raw, headers = _signed({"recipient": "x@y", "raw_base64": ""})
            assert (
                await c.post(
                    "/api/v1/public/email-identity/inbound",
                    content=raw,
                    headers=headers,
                )
            ).status_code == 404

    async def test_unallocated_shows_the_next_step_and_the_card_row(self, team, on):
        async with client_as(team.as_user(team.member)) as c:
            body = (await c.get("/api/v1/me/email-identity")).json()
        assert (
            body["state"] == "unallocated" and body["next_step"] == "Choose an address."
        )
        assert body["address"] is None and body["card_interest"] is False


@pytest.mark.asyncio
class TestChoosingAName:
    @pytest.mark.parametrize(
        "alias,reason",
        [
            ("ab", "invalid"),
            ("-meera", "invalid"),
            ("me..era", "invalid"),
            ("postmaster", "reserved"),
            ("Support", "reserved"),
        ],
    )
    async def test_specific_reasons(self, team, on, alias, reason):
        checked = await email_identity.check(team.member.id, alias)
        assert checked["available"] is False and checked["reason"] == reason

    async def test_taken_never_says_by_whom(self, team, on):
        await email_identity.reserve(team.owner.id, team.org, _name(team, "x"))
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(
                "/api/v1/me/email-identity/check", json={"alias": _name(team, "x")}
            )
        body = r.json()
        assert body["reason"] == "taken"
        assert str(team.owner.id) not in json.dumps(body).replace(_name(team, "x"), "")

    async def test_two_requests_for_one_name_cannot_both_win(self, team, on):
        alias = _name(team, "race")
        results = await asyncio.gather(
            email_identity.reserve(team.member.id, team.org, alias),
            email_identity.reserve(team.owner.id, team.org, alias),
            return_exceptions=True,
        )
        wins = [r for r in results if isinstance(r, dict)]
        losses = [r for r in results if isinstance(r, email_identity.AliasError)]
        assert len(wins) == 1 and len(losses) == 1 and losses[0].code == "taken"

    async def test_one_address_per_person(self, team, on):
        await email_identity.reserve(team.member.id, team.org, _name(team, "a"))
        with pytest.raises(email_identity.AliasError) as caught:
            await email_identity.reserve(team.member.id, team.org, _name(team, "b"))
        assert caught.value.code == "has_one"

    async def test_a_given_up_name_is_never_someone_elses(self, team, on):
        alias = _name(team, "old")
        await email_identity.reserve(team.member.id, team.org, alias)
        await email_identity.release(team.member.id)
        checked = await email_identity.check(team.owner.id, alias)
        assert checked["reason"] == "taken"


@pytest.mark.asyncio
class TestActiveOnlyAfterDelivery:
    async def test_no_inbound_secret_is_needs_setup(self, team, on, mail, monkeypatch):
        monkeypatch.setattr(constants, "EMAIL_IDENTITY_WEBHOOK_SECRET", "")
        await email_identity.reserve(team.member.id, team.org, _name(team))
        with pytest.raises(email_identity.NeedsSetup):
            await email_identity.provision(team.member.id)
        assert (await email_identity.view(team.member.id))["state"] == "reserved"

    async def test_provisioning_then_active_when_the_check_arrives(
        self, team, on, mail
    ):
        alias = _name(team)
        await email_identity.reserve(team.member.id, team.org, alias)
        view = await email_identity.provision(team.member.id)
        assert view["state"] == "provisioning" and view["address"] is None
        assert mail.await_args.kwargs["to"] == f"{alias}@decibyl.test"
        # A message without the token does not activate it.
        status = await email_identity.receive(
            f"{alias}@decibyl.test",
            _mime(
                f"{alias}@decibyl.test",
                "Decibyl address check guessed",
                message_id="<g@x>",
            ),
        )
        assert status == "delivered"
        assert (await email_identity.view(team.member.id))["state"] == "provisioning"
        subject = mail.await_args.kwargs["subject"]
        await email_identity.receive(
            f"{alias}@decibyl.test",
            _mime(f"{alias}@decibyl.test", subject, message_id="<p@x>"),
        )
        view = await email_identity.view(team.member.id)
        assert view["state"] == "active" and view["address"] == f"{alias}@decibyl.test"

    async def test_an_active_address_is_not_given_up_from_here(self, team, on, mail):
        await _activate(team, mail)
        with pytest.raises(email_identity.AliasError) as caught:
            await email_identity.release(team.member.id)
        assert caught.value.code == "active"


@pytest.mark.asyncio
class TestInbound:
    async def test_a_bad_or_old_signature_is_refused(self, team, on):
        raw, headers = _signed({"recipient": "a@decibyl.test", "raw_base64": "eA=="})
        async with client_as(team.as_user(team.member)) as c:
            bad = dict(headers, **{"X-Decibyl-Signature": "sha256=" + "0" * 64})
            assert (
                await c.post(
                    "/api/v1/public/email-identity/inbound", content=raw, headers=bad
                )
            ).status_code == 403
        assert not email_identity.verify(
            raw,
            headers["X-Decibyl-Signature"],
            headers["X-Decibyl-Timestamp"],
            now=time.time() + 3600,
        )

    async def test_routed_to_its_owner_threaded_and_once(self, team, on, mail):
        alias = await _activate(team, mail)
        to = f"{alias}@decibyl.test"
        first = _mime(to, "Invoice", message_id="<m1@example.com>")
        reply = _mime(
            to,
            "Re: Invoice",
            message_id="<m2@example.com>",
            references="<m1@example.com>",
        )
        raw, headers = _signed(
            {"recipient": to, "raw_base64": base64.b64encode(first).decode()}
        )
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(
                "/api/v1/public/email-identity/inbound", content=raw, headers=headers
            )
            assert r.json() == {"status": "delivered"}
            again = await c.post(
                "/api/v1/public/email-identity/inbound", content=raw, headers=headers
            )
            assert again.json() == {"status": "duplicate"}
        assert await email_identity.receive(to, reply) == "delivered"
        threads = await email_identity.threads(team.member.id)
        assert len(threads) == 1 and threads[0]["messages"] == 2
        assert threads[0]["thread_key"] == "<m1@example.com>"
        # And only the owner sees it.
        assert await email_identity.threads(team.owner.id) == []
        async with client_as(team.as_user(team.owner)) as c:
            assert (await c.get("/api/v1/me/email-identity")).json()["threads"] == []

    async def test_mail_for_nobody_is_quarantined_and_shown_to_nobody(self, team, on):
        status = await email_identity.receive(
            "nobody-here@decibyl.test",
            _mime(
                "nobody-here@decibyl.test", "hi", message_id=f"<q{team.member.id}@x>"
            ),
        )
        assert status == "quarantined"
        async with db_client.async_session() as session:
            row = await session.scalar(
                select(EmailIdentityMessageModel).where(
                    EmailIdentityMessageModel.message_id == f"<q{team.member.id}@x>"
                )
            )
        assert row.user_id is None and row.body_text is None

    async def test_too_large_is_refused(self, team, on, mail, monkeypatch):
        alias = await _activate(team, mail)
        monkeypatch.setattr(constants, "EMAIL_IDENTITY_MAX_INBOUND_BYTES", 100)
        status = await email_identity.receive(
            f"{alias}@decibyl.test",
            _mime(f"{alias}@decibyl.test", "big", message_id="<big@x>"),
        )
        assert status == "too_large"

    async def test_a_dangerous_attachment_is_marked_and_the_check_is_named(
        self, team, on, mail
    ):
        alias = await _activate(team, mail)
        await email_identity.receive(
            f"{alias}@decibyl.test",
            _mime(
                f"{alias}@decibyl.test",
                "tool",
                message_id="<att@x>",
                attach="setup.exe",
            ),
        )
        (thread,) = await email_identity.threads(team.member.id)
        (attachment,) = thread["attachments"]
        assert attachment["blocked"] is True and attachment["check"] == "file_type_only"


@pytest.mark.asyncio
class TestSending:
    @pytest.fixture
    def outbound(self, monkeypatch):
        monkeypatch.setattr(constants, "EMAIL_IDENTITY_OUTBOUND_VERIFIED", True)
        sent = []
        monkeypatch.setattr(
            email_identity, "_smtp_send", lambda message: sent.append(message)
        )
        return sent

    async def _card(self, team):
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(
                "/api/v1/me/email-identity/send",
                json={
                    "to": "ravi@example.com",
                    "subject": "Invoice",
                    "body": "Attached.",
                },
            )
        return r

    async def test_not_set_up_for_sending_is_said_not_attempted(
        self, team, on, mail, no_queue
    ):
        await _activate(team, mail)
        r = await self._card(team)
        assert r.status_code == 409 and "not set up" in r.json()["detail"]

    async def test_the_card_shows_the_exact_account_and_sends_once(
        self, team, on, mail, outbound, no_queue
    ):
        alias = await _activate(team, mail)
        r = await self._card(team)
        assert r.status_code == 200, r.text
        card = r.json()
        assert card["args"]["from_address"] == f"{alias}@decibyl.test"
        assert "not from a connected mailbox" in card["effect"]
        await actions.settle(
            organization_id=team.org,
            event_id=card["event_id"],
            verb="confirm",
            user_id=team.member.id,
            version=card["version"],
        )
        await actions.run(card["event_id"], team.org)
        (message,) = outbound
        assert (
            message["From"] == f"{alias}@decibyl.test"
            and message["To"] == "ravi@example.com"
        )
        done = await db_client.get_agent_event(
            card["event_id"], organization_id=team.org
        )
        assert done.payload["state"] == "done"
        # Run again (a duplicate job): nothing more is sent.
        await actions.run(card["event_id"], team.org)
        with pytest.raises(cards.CardError):
            await email_identity.send(
                team.member.id,
                identity_id=card["args"].get("identity_id")
                or done.payload["args"]["identity_id"],
                card_event_id=card["event_id"],
                to="ravi@example.com",
                subject="Invoice",
                body="Attached.",
            )
        assert len(outbound) == 1

    async def test_a_lost_answer_from_the_server_is_outcome_unknown(
        self, team, on, mail, outbound, no_queue, monkeypatch
    ):
        await _activate(team, mail)

        def timeout(message):
            raise TimeoutError("server went quiet")

        monkeypatch.setattr(email_identity, "_smtp_send", timeout)
        card = (await self._card(team)).json()
        await actions.settle(
            organization_id=team.org,
            event_id=card["event_id"],
            verb="confirm",
            user_id=team.member.id,
            version=card["version"],
        )
        await actions.run(card["event_id"], team.org)
        unknown = await db_client.get_agent_event(
            card["event_id"], organization_id=team.org
        )
        assert unknown.payload["state"] == "outcome_unknown"
        row = await email_identity.send_for_card(card["event_id"])
        assert row.state == "sending"

    async def test_editing_the_card_is_a_new_version(
        self, team, on, mail, outbound, no_queue
    ):
        await _activate(team, mail)
        card = (await self._card(team)).json()
        revised = await actions.revise(
            organization_id=team.org,
            event_id=card["event_id"],
            arguments={"subject": "Invoice 42"},
            user_id=team.member.id,
        )
        assert (
            revised["version"] != card["version"]
            and revised["args"]["subject"] == "Invoice 42"
        )
        with pytest.raises(actions.ActionError):
            await actions.settle(
                organization_id=team.org,
                event_id=card["event_id"],
                verb="confirm",
                user_id=team.member.id,
                version=card["version"],
            )

    async def test_a_bounce_and_a_complaint_change_the_state(
        self, team, on, mail, outbound, no_queue
    ):
        await _activate(team, mail)
        card = (await self._card(team)).json()
        await actions.settle(
            organization_id=team.org,
            event_id=card["event_id"],
            verb="confirm",
            user_id=team.member.id,
            version=card["version"],
        )
        await actions.run(card["event_id"], team.org)
        sent = await email_identity.send_for_card(card["event_id"])
        raw, headers = _signed(
            {"events": [{"type": "bounce", "message_id": sent.message_id}]}
        )
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(
                "/api/v1/public/email-identity/events", content=raw, headers=headers
            )
        assert r.json() == {"applied": 1}
        assert (await email_identity.view(team.member.id))["state"] == "delivery_issue"
        await email_identity.provider_events(
            [{"type": "complaint", "message_id": sent.message_id}]
        )
        view = await email_identity.view(team.member.id)
        assert view["state"] == "suspended" and "paused" in view["next_step"]
        async with db_client.async_session() as session:
            row = await session.scalar(
                select(EmailIdentitySendModel).where(
                    EmailIdentitySendModel.card_event_id == card["event_id"]
                )
            )
        assert row.state == "complained"


@pytest.mark.asyncio
class TestVirtualCard:
    async def test_interest_only(self, team, on):
        async with client_as(team.as_user(team.member)) as c:
            r = await c.put("/api/v1/me/card-interest", json={"interested": True})
            assert r.json() == {"interested": True}
            assert (await c.get("/api/v1/me/email-identity")).json()[
                "card_interest"
            ] is True
            # Nothing but a yes or no is accepted.
            bad = await c.put(
                "/api/v1/me/card-interest", json={"interested": True, "number": "4111"}
            )
            assert bad.status_code == 422
