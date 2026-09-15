"""Inbound email triggers (KAN-138): mail to a bot's address fires the bot.

Two halves, mirroring the webhook doorbell it is built beside:

- the pure functions that turn a provider's POST into one shape and find the
  trigger uuid inside a recipient address, whatever envelope it arrived in;
- the public route, which must answer 200 with a *status* for everything a
  mail provider might send -- unknown address, paused, duplicate, filtered --
  so the provider never bounces a message back to a real sender, and must run
  the bot exactly once for a message that matches.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api import constants
from api.services.workflow import bot_triggers

UUID = "11111111-2222-3333-4444-555555555555"


class TestTheAddress:
    def test_the_address_is_the_uuid_at_the_domain(self):
        with patch.object(bot_triggers, "INBOUND_EMAIL_DOMAIN", "in.decibyl.ai"):
            assert bot_triggers.inbound_address(UUID) == f"{UUID}@in.decibyl.ai"

    def test_a_bare_recipient_yields_the_uuid(self):
        assert bot_triggers.address_uuid(f"{UUID}@in.decibyl.ai") == UUID

    def test_a_display_name_recipient_yields_the_uuid(self):
        got = bot_triggers.address_uuid(f'"Ops Inbox" <{UUID}@in.decibyl.ai>')
        assert got == UUID

    def test_plus_addressing_is_stripped_to_the_uuid(self):
        assert bot_triggers.address_uuid(f"{UUID}+shopify@in.decibyl.ai") == UUID

    def test_the_uuid_is_lowercased(self):
        assert bot_triggers.address_uuid(f"{UUID.upper()}@IN.DECIBYL.AI") == UUID

    def test_a_recipient_without_an_at_is_no_recipient(self):
        assert bot_triggers.address_uuid("not-an-address") is None
        assert bot_triggers.address_uuid("") is None


class TestNormalisingTheProvidersShape:
    def test_sendgrid_field_names(self):
        out = bot_triggers.normalise_email(
            {
                "to": f"{UUID}@in.decibyl.ai",
                "from": "meera@shop.example",
                "subject": "Order 91",
                "text": "please check stock",
                "message-id": "<a@x>",
            }
        )
        assert out["recipient"] == f"{UUID}@in.decibyl.ai"
        assert out["from"] == "meera@shop.example"
        assert out["subject"] == "Order 91"
        assert out["text"] == "please check stock"
        assert out["message_id"] == "<a@x>"

    def test_postmark_field_names(self):
        out = bot_triggers.normalise_email(
            {
                "OriginalRecipient": f"{UUID}@in.decibyl.ai",
                "From": "meera@shop.example",
                "Subject": "Order 91",
                "TextBody": "check stock",
                "MessageID": "pm-1",
            }
        )
        assert out["recipient"] == f"{UUID}@in.decibyl.ai"
        assert out["from"] == "meera@shop.example"
        assert out["text"] == "check stock"
        assert out["message_id"] == "pm-1"

    def test_mailgun_field_names(self):
        out = bot_triggers.normalise_email(
            {
                "recipient": f"{UUID}@in.decibyl.ai",
                "sender": "meera@shop.example",
                "body-plain": "check stock",
            }
        )
        assert out["recipient"] == f"{UUID}@in.decibyl.ai"
        assert out["from"] == "meera@shop.example"
        assert out["text"] == "check stock"

    def test_the_recipient_is_read_out_of_an_envelope_blob(self):
        out = bot_triggers.normalise_email(
            {
                "from": "meera@shop.example",
                "envelope": f'{{"to": ["{UUID}@in.decibyl.ai"], "from": "x@y"}}',
            }
        )
        assert out["recipient"] == f"{UUID}@in.decibyl.ai"

    def test_the_body_is_bounded(self):
        out = bot_triggers.normalise_email(
            {"text": "x" * (bot_triggers.MAX_PAYLOAD_CHARS + 500)}
        )
        assert len(out["text"]) <= bot_triggers.MAX_PAYLOAD_CHARS + 32
        assert out["text"].endswith("(truncated)")


def _trigger(**overrides):
    """A stored email trigger, as the DB client would return it."""
    base = {
        "id": 7,
        "organization_id": 1,
        "workflow_id": 2,
        "uuid": UUID,
        "secret": "s3cret",
        "name": "Supplier mail",
        "source": "email",
        "sentence": "when a supplier emails, log it",
        "instruction": "Log the message.",
        "fields": [],
        "filter": [],
        "is_active": True,
        "created_by": 1,
        "created_at": None,
        "last_fired_at": None,
        "fired_count": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
class TestOurOwnMailNeverFiresABot:
    """A bot's reply to the address that triggered it must not trigger it."""

    def test_mail_from_the_inbound_domain_is_ours(self):
        assert bot_triggers.is_own_mail(f"{UUID}@in.decibyl.ai")
        assert bot_triggers.is_own_mail(f"Front desk <{UUID}@IN.decibyl.ai>")

    def test_mail_from_a_platform_sender_is_ours(self):
        with (
            patch.object(constants, "EMAIL_FROM_ADDRESS", "hello@decibyl.ai"),
            patch.object(constants, "EMAIL_FROM_BILLING", "billing@decibyl.ai"),
        ):
            assert bot_triggers.is_own_mail("hello@decibyl.ai")
            assert bot_triggers.is_own_mail("Decibyl Billing <Billing@decibyl.ai>")

    def test_a_customer_on_our_sending_domain_is_not_ours(self):
        """Only the exact sending addresses count, not everyone at decibyl.ai."""
        with patch.object(constants, "EMAIL_FROM_ADDRESS", "hello@decibyl.ai"):
            assert not bot_triggers.is_own_mail("someone@decibyl.ai")

    def test_ordinary_senders_and_junk_are_not_ours(self):
        assert not bot_triggers.is_own_mail("meera@shop.example")
        assert not bot_triggers.is_own_mail("")
        assert not bot_triggers.is_own_mail(None)
        assert not bot_triggers.is_own_mail("not an address")


class TestTheInboundRoute:
    """The public route, with the store and the queue stood in for."""

    async def _post(self, trigger, *, headers=None, params=None, json=None, data=None):
        from api.app import app
        from api.routes import public_email as route

        with (
            patch.object(
                route.db_client,
                "get_bot_trigger_by_uuid",
                AsyncMock(return_value=trigger),
            ),
            patch.object(route, "already_delivered", AsyncMock(return_value=None)),
            patch.object(route, "count_trigger", AsyncMock()),
            patch.object(route, "remember_delivery", AsyncMock()) as remember,
            patch.object(route, "enqueue_job", AsyncMock()) as enqueue,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/public/email/inbound",
                    headers=headers or {},
                    params=params,
                    json=json,
                    data=data,
                )
        return response, enqueue, remember

    async def test_a_matching_message_is_queued_and_remembered(self):
        response, enqueue, remember = await self._post(
            _trigger(),
            json={
                "to": f"{UUID}@in.decibyl.ai",
                "from": "meera@shop.example",
                "subject": "Order 91",
                "text": "check stock",
                "message-id": "<m1@x>",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"
        args = enqueue.await_args.args
        assert args[0] == "run_bot_trigger" and args[1] == 7
        assert args[2]["from"] == "meera@shop.example" and args[3] == "<m1@x>"
        assert remember.await_args.args[1] == "<m1@x>"

    async def test_a_bots_own_reply_is_dropped_before_anything_is_counted(self):
        response, enqueue, remember = await self._post(
            _trigger(),
            json={
                "to": f"{UUID}@in.decibyl.ai",
                "from": f"Orders bot <{UUID}@in.decibyl.ai>",
                "subject": "Re: Order 91",
                "text": "Noted, thanks.",
                "message-id": "<reply-1@x>",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "own_mail"
        assert not enqueue.await_count
        assert not remember.await_count

    async def test_a_message_with_no_recipient_does_nothing(self):
        response, enqueue, _ = await self._post(
            _trigger(), json={"from": "meera@shop.example", "text": "hi"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "no_recipient"
        enqueue.assert_not_awaited()

    async def test_a_webhook_trigger_at_an_email_address_is_unknown(self):
        # source guard: only email triggers answer here, so a webhook trigger
        # that happens to share the uuid space is not fired by mail.
        response, enqueue, _ = await self._post(
            _trigger(source="webhook"),
            json={"to": f"{UUID}@in.decibyl.ai", "text": "hi"},
        )
        assert response.json()["status"] == "unknown_address"
        enqueue.assert_not_awaited()

    async def test_an_unknown_address_does_not_bounce(self):
        response, enqueue, _ = await self._post(
            None, json={"to": f"{UUID}@in.decibyl.ai", "text": "hi"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "unknown_address"
        enqueue.assert_not_awaited()

    async def test_a_paused_trigger_still_answers_200(self):
        response, enqueue, _ = await self._post(
            _trigger(is_active=False),
            json={"to": f"{UUID}@in.decibyl.ai", "text": "hi"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "paused"
        enqueue.assert_not_awaited()

    async def test_a_message_outside_the_filter_costs_nothing(self):
        response, enqueue, remember = await self._post(
            _trigger(filter=[{"field": "subject", "op": "contains", "value": "order"}]),
            json={"to": f"{UUID}@in.decibyl.ai", "subject": "hello", "text": "hi"},
        )
        assert response.json()["status"] == "filtered"
        enqueue.assert_not_awaited()
        remember.assert_not_awaited()

    async def test_form_encoded_mail_is_accepted(self):
        # SendGrid's inbound parse posts multipart/form, not JSON.
        response, enqueue, _ = await self._post(
            _trigger(),
            data={"to": f"{UUID}@in.decibyl.ai", "from": "m@x", "text": "hi"},
        )
        assert response.status_code == 200 and response.json()["status"] == "accepted"
        assert enqueue.await_args.args[2]["from"] == "m@x"

    async def test_a_redelivery_is_not_a_second_run(self):
        from api.app import app
        from api.routes import public_email as route

        with (
            patch.object(
                route.db_client,
                "get_bot_trigger_by_uuid",
                AsyncMock(return_value=_trigger()),
            ),
            patch.object(route, "already_delivered", AsyncMock(return_value=0)),
            patch.object(route, "enqueue_job", AsyncMock()) as enqueue,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/public/email/inbound",
                    json={
                        "to": f"{UUID}@in.decibyl.ai",
                        "message-id": "<m1@x>",
                        "text": "hi",
                    },
                )
        assert response.json()["status"] == "duplicate"
        enqueue.assert_not_awaited()


@pytest.mark.asyncio
class TestTheInboundToken:
    async def _post_with_token(self, *, token_env, headers=None, params=None):
        from api.app import app
        from api.routes import public_email as route

        with (
            patch.object(route, "INBOUND_EMAIL_TOKEN", token_env),
            patch.object(
                route.db_client,
                "get_bot_trigger_by_uuid",
                AsyncMock(return_value=_trigger()),
            ),
            patch.object(route, "already_delivered", AsyncMock(return_value=None)),
            patch.object(route, "count_trigger", AsyncMock()),
            patch.object(route, "remember_delivery", AsyncMock()),
            patch.object(route, "enqueue_job", AsyncMock()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.post(
                    "/api/v1/public/email/inbound",
                    headers=headers or {},
                    params=params,
                    json={"to": f"{UUID}@in.decibyl.ai", "text": "hi"},
                )

    async def test_a_missing_token_is_refused_when_one_is_set(self):
        resp = await self._post_with_token(token_env="sekret")
        assert resp.status_code == 401

    async def test_the_token_may_come_in_a_header(self):
        resp = await self._post_with_token(
            token_env="sekret", headers={"X-Inbound-Token": "sekret"}
        )
        assert resp.status_code == 200 and resp.json()["status"] == "accepted"

    async def test_the_token_may_come_as_a_query_param(self):
        resp = await self._post_with_token(
            token_env="sekret", params={"token": "sekret"}
        )
        assert resp.status_code == 200 and resp.json()["status"] == "accepted"


class TestTheAddressOnTheResponse:
    """_render fills the forward-to address for an email trigger, not a webhook."""

    def test_an_email_trigger_carries_its_address(self):
        from api.routes.bot_triggers import _render

        with patch.object(bot_triggers, "INBOUND_EMAIL_DOMAIN", "in.decibyl.ai"):
            rendered = _render(_trigger())
        assert rendered.address == f"{UUID}@in.decibyl.ai"

    def test_a_webhook_trigger_has_no_address(self):
        from api.routes.bot_triggers import _render

        rendered = _render(_trigger(source="webhook"))
        assert rendered.address is None
