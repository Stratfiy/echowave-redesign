"""The platform WhatsApp number hears (A3 groundwork): only Meta may post,
only a linked number is anybody's, a file becomes a document, twice is
once, and the answer goes back the way it came."""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api import constants
from api.services.messaging import whatsapp_inbound as wa

SECRET = "app-secret"
OWN = "+919876543210"


def _payload(messages: list[dict], name: str = "Meera") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "1050"},
                            "contacts": [
                                {"wa_id": "919876543210", "profile": {"name": name}}
                            ],
                            "messages": messages,
                        }
                    }
                ]
            }
        ],
    }


def _text(body: str, mid: str = "wamid.t1") -> dict:
    return {"id": mid, "from": "919876543210", "type": "text", "text": {"body": body}}


def _doc(mid: str = "wamid.d1") -> dict:
    return {
        "id": mid,
        "from": "919876543210",
        "type": "document",
        "document": {
            "id": "media-9",
            "mime_type": "application/pdf",
            "filename": "LIC policy.pdf",
            "caption": "my policy",
        },
    }


def _sign(raw: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


class TestOnlyMetaMayPost:
    def test_a_good_signature_passes_and_a_bad_one_does_not(self):
        raw = b'{"x":1}'
        with patch.object(constants, "WHATSAPP_APP_SECRET", SECRET):
            assert wa.verify_signature(raw, _sign(raw))
            assert not wa.verify_signature(raw, "sha256=" + "0" * 64)
            assert not wa.verify_signature(raw, None)

    def test_no_secret_configured_means_nothing_verifies(self):
        with patch.object(constants, "WHATSAPP_APP_SECRET", ""):
            assert not wa.verify_signature(b"x", _sign(b"x"))

    def test_the_registration_challenge_answers_our_token_only(self):
        with patch.object(constants, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "tok"):
            assert wa.challenge_for("subscribe", "tok", "12345") == "12345"
            assert wa.challenge_for("subscribe", "nope", "12345") is None
            assert wa.challenge_for("unsubscribe", "tok", "12345") is None


class TestParsing:
    def test_text_and_document_messages_with_the_senders_name(self):
        got = wa.parse(_payload([_text("hi"), _doc()]))
        assert [m.kind for m in got] == ["text", "document"]
        assert (
            got[0].sender == OWN
            and got[0].sender_name == "Meera"
            and got[0].text == "hi"
        )
        assert got[1].media_id == "media-9" and got[1].filename == "LIC policy.pdf"
        assert got[1].text == "my policy" and got[1].phone_number_id == "1050"

    def test_statuses_and_unknown_types_are_skipped(self):
        payload = _payload(
            [{"id": "x", "from": "919876543210", "type": "sticker", "sticker": {}}]
        )
        payload["entry"][0]["changes"][0]["value"]["statuses"] = [
            {"id": "s", "status": "read"}
        ]
        assert wa.parse(payload) == []
        assert wa.parse({"nonsense": True}) == []


MEERA = SimpleNamespace(id=3, provider_id="u3")
LINKED = SimpleNamespace(id=11, organization_id=7, user_id=3)


def _inbound(kind="text", text="where is my aadhaar", mid="wamid.1"):
    return wa.Inbound(
        message_id=mid,
        sender=OWN,
        sender_name="Meera",
        kind=kind,
        text=text,
        media_id="media-9" if kind != "text" else None,
        mime_type="application/pdf" if kind != "text" else None,
        filename="LIC policy.pdf" if kind != "text" else None,
    )


@pytest.mark.asyncio
class TestStrangersAreStrangers:
    """Launch stream identity: a number nobody linked is nobody's member.

    It used to be answered as the workspace's first member whenever the
    number was one that workspace had verified for test calls -- their
    Gmail, their memory, their cards. These fail on that code."""

    @pytest.fixture(autouse=True)
    def _nobody_is_linked(self):
        with patch(
            "api.services.messaging.channels.identities.find",
            AsyncMock(return_value=None),
        ):
            yield

    @pytest.fixture
    def greeted(self):
        sent = AsyncMock()
        adapter = SimpleNamespace(enabled=lambda: True, send_text=sent)
        with patch(
            "api.services.messaging.channels.dispatch.adapter_for",
            lambda channel: adapter,
        ):
            yield sent

    async def test_a_verified_but_unlinked_number_is_not_the_first_member(
        self, greeted
    ):
        ask = AsyncMock(return_value=[])
        first = AsyncMock(return_value=[MEERA])
        with (
            patch.object(
                wa.db_client,
                "find_organization_by_verified_number",
                AsyncMock(return_value=7),
            ),
            patch.object(wa.db_client, "get_organization_users", first),
            patch.object(wa, "seen_before", AsyncMock(return_value=False)),
            patch.object(wa, "touch_session", AsyncMock()),
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert await wa.handle(_inbound()) == "unlinked"
        assert ask.await_count == 0
        assert first.await_count == 0
        # What must appear: the same "link me first" every channel gives.
        from api.services.messaging.channels.dispatch import LINK_FIRST

        greeted.assert_awaited_once_with({"to": OWN}, LINK_FIRST)

    async def test_a_strangers_document_is_not_filed_anywhere(self, greeted):
        ask = AsyncMock()
        download = AsyncMock(return_value=(b"%PDF", "application/pdf"))
        create = AsyncMock()
        with (
            patch.object(
                wa.db_client,
                "find_organization_by_verified_number",
                AsyncMock(return_value=7),
            ),
            patch.object(wa, "seen_before", AsyncMock(return_value=False)),
            patch.object(wa, "touch_session", AsyncMock()),
            patch.object(wa, "download_media", download),
            patch.object(wa.db_client, "create_document", create),
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert await wa.handle(_inbound(kind="document")) == "unlinked"
        assert download.await_count == 0 and create.await_count == 0
        assert ask.await_count == 0

    async def test_an_unverified_number_is_a_stranger_too(self, greeted):
        ask = AsyncMock()
        with (
            patch.object(wa, "seen_before", AsyncMock(return_value=False)),
            patch.object(wa, "touch_session", AsyncMock()),
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert await wa.handle(_inbound()) == "unlinked"
        assert ask.await_count == 0
        greeted.assert_awaited_once()

    async def test_a_strangers_redelivery_is_greeted_once(self, greeted):
        with patch.object(wa, "seen_before", AsyncMock(side_effect=[False, True])):
            with patch.object(wa, "touch_session", AsyncMock()):
                assert await wa.handle(_inbound()) == "unlinked"
                assert await wa.handle(_inbound()) == "duplicate"
        assert greeted.await_count == 1

    async def test_a_stranger_can_still_link_with_a_code(self):
        handled = AsyncMock(return_value="linked")
        with (
            patch.object(wa, "seen_before", AsyncMock(return_value=False)),
            patch.object(wa, "touch_session", AsyncMock()),
            patch("api.services.messaging.channels.dispatch.handle", handled),
        ):
            assert await wa.handle(_inbound(text="LINK 4KX9QZ")) == "linked"
        assert handled.await_args.args[0].text == "LINK 4KX9QZ"


@pytest.mark.asyncio
class TestRouting:
    """A linked number is that member: their Gmail, their memory."""

    @pytest.fixture(autouse=True)
    def _linked(self):
        with (
            patch(
                "api.services.messaging.channels.identities.find",
                AsyncMock(return_value=LINKED),
            ),
            patch.object(wa.db_client, "get_user_by_id", AsyncMock(return_value=MEERA)),
        ):
            yield

    async def test_a_linked_number_reaches_its_members_decibyl_and_replies_there(
        self,
    ):
        ask = AsyncMock(return_value=[])
        with (
            patch.object(wa, "seen_before", AsyncMock(return_value=False)),
            patch.object(wa, "touch_session", AsyncMock()) as touch,
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert await wa.handle(_inbound()) == "accepted"
        touch.assert_awaited_once_with(OWN)
        kwargs = ask.await_args.kwargs
        assert kwargs["organization_id"] == 7 and kwargs["user_id"] == 3
        assert kwargs["text"] == "where is my aadhaar"
        # The ref is what the channel dispatcher replies with (KAN-277).
        assert kwargs["reply_to"] == {
            "channel": "whatsapp",
            "to": OWN,
            "ref": {"to": OWN},
        }

    async def test_a_redelivery_answers_nothing_twice(self):
        ask = AsyncMock()
        with (
            patch.object(wa, "seen_before", AsyncMock(return_value=True)),
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert await wa.handle(_inbound()) == "duplicate"
        assert ask.await_count == 0

    async def test_a_document_is_filed_and_reaches_decibyl_as_an_attachment(self):
        ask = AsyncMock(return_value=[])
        create = AsyncMock(return_value=SimpleNamespace(id=55))
        enqueue = AsyncMock()
        with (
            patch.object(wa, "seen_before", AsyncMock(return_value=False)),
            patch.object(wa, "touch_session", AsyncMock()),
            patch.object(
                wa,
                "download_media",
                AsyncMock(return_value=(b"%PDF", "application/pdf")),
            ),
            patch(
                "api.services.storage.storage_fs.acreate_file_from_bytes",
                AsyncMock(return_value=True),
            ) as store,
            patch.object(wa.db_client, "create_document", create),
            patch("api.tasks.arq.enqueue_job", enqueue),
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert (
                await wa.handle(_inbound(kind="document", text="my policy"))
                == "accepted"
            )
        key = store.await_args.args[0]
        assert key.endswith("/LIC_policy.pdf") and "/7/" in key  # safe_filename
        assert create.await_args.kwargs["custom_metadata"]["source"] == "whatsapp"
        assert create.await_args.kwargs["mime_type"] == "application/pdf"
        assert enqueue.await_args.args[0] == "process_knowledge_base_document"
        attachments = ask.await_args.kwargs["attachments"]
        assert (
            attachments[0]["filename"] == "LIC policy.pdf"
            and attachments[0]["size_bytes"] == 4
        )
        assert ask.await_args.kwargs["text"] == "my policy"

    async def test_a_file_that_cannot_be_fetched_still_reaches_the_thread_as_words(
        self,
    ):
        ask = AsyncMock(return_value=[])
        with (
            patch.object(wa, "seen_before", AsyncMock(return_value=False)),
            patch.object(wa, "touch_session", AsyncMock()),
            patch.object(
                wa, "download_media", AsyncMock(side_effect=ValueError("gone"))
            ),
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert await wa.handle(_inbound(kind="image", text="")) == "accepted"
        assert "could not be received" in ask.await_args.kwargs["text"]
        assert ask.await_args.kwargs["attachments"] == []


@pytest.mark.asyncio
class TestTheReplyGoesBack:
    async def test_the_answer_is_sent_on_whatsapp_and_charged(self):
        send = AsyncMock(
            return_value=SimpleNamespace(ok=True, message_id="wamid.r", error=None)
        )
        debit = AsyncMock(return_value=100)
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "api.services.messaging.platform_whatsapp.is_configured", lambda: True
            ),
            patch(
                "api.services.messaging.platform_whatsapp.credentials",
                lambda: {"access_token": "t", "phone_number_id": "p"},
            ),
            patch("api.services.messaging.send.send_message", send),
            patch("api.services.billing.messaging_charges.debit_message", debit),
            patch.object(wa.db_client, "async_session", return_value=session),
        ):
            await wa.reply(organization_id=7, to=OWN, body="Found it: https://drive/f1")
        assert send.await_args.kwargs["to"] == OWN
        assert debit.await_args.kwargs["message_id"] == "wamid.r"

    async def test_the_job_replies_only_when_the_line_came_from_whatsapp(self):
        from api.tasks import routines

        reply = AsyncMock()
        with (
            patch("api.services.workflow.decibyl.answer", AsyncMock(return_value="ok")),
            patch.object(wa, "reply", reply),
        ):
            await routines.answer_decibyl_message(
                None, 7, "hi", reply_to={"channel": "whatsapp", "to": OWN}
            )
            await routines.answer_decibyl_message(None, 7, "hi")
        assert reply.await_count == 1
        assert reply.await_args.kwargs == {
            "organization_id": 7,
            "to": OWN,
            "body": "ok",
        }


@pytest.mark.asyncio
class TestTheRoute:
    async def _post(self, payload: dict, *, signed: bool = True):
        from api.app import app

        raw = json.dumps(payload).encode()
        headers = {"content-type": "application/json"}
        if signed:
            headers["X-Hub-Signature-256"] = _sign(raw)
        with (
            patch.object(constants, "WHATSAPP_APP_SECRET", SECRET),
            patch.object(wa, "handle", AsyncMock(return_value="accepted")) as handle,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/public/whatsapp/webhook", content=raw, headers=headers
                )
        return response, handle

    async def test_a_signed_post_is_handled_and_answered_200(self):
        response, handle = await self._post(_payload([_text("hi")]))
        assert response.status_code == 200 and response.json()["messages"] == [
            "accepted"
        ]
        assert handle.await_args.args[0].text == "hi"

    async def test_an_unsigned_post_is_refused_before_parsing(self):
        response, handle = await self._post(_payload([_text("hi")]), signed=False)
        assert response.status_code == 403 and handle.await_count == 0

    async def test_the_registration_get(self):
        from api.app import app

        with patch.object(constants, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "tok"):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                ok = await client.get(
                    "/api/v1/public/whatsapp/webhook",
                    params={
                        "hub.mode": "subscribe",
                        "hub.verify_token": "tok",
                        "hub.challenge": "777",
                    },
                )
                bad = await client.get(
                    "/api/v1/public/whatsapp/webhook",
                    params={
                        "hub.mode": "subscribe",
                        "hub.verify_token": "x",
                        "hub.challenge": "777",
                    },
                )
        assert ok.status_code == 200 and ok.text == "777"
        assert bad.status_code == 403


@pytest.mark.asyncio
class TestTheWindowGatesAFile:
    async def test_a_closed_window_with_no_template_is_an_honest_refusal(self):
        from api.services.workflow import documents

        send = AsyncMock()
        with (
            patch.object(
                documents, "own_channels", AsyncMock(return_value=({OWN}, set()))
            ),
            patch.object(
                documents,
                "download",
                AsyncMock(return_value=(b"%PDF", "a.pdf", "application/pdf")),
            ),
            patch(
                "api.services.messaging.platform_whatsapp.is_configured", lambda: True
            ),
            patch.object(wa, "session_open", AsyncMock(return_value=False)),
            patch.object(constants, "WHATSAPP_FILE_OFFER_TEMPLATE", ""),
            patch("api.services.messaging.send.send_message", send),
        ):
            with pytest.raises(documents.DocumentError, match="24 hours"):
                await documents.deliver(
                    7,
                    file_id="f",
                    name="Rent.pdf",
                    channel="whatsapp",
                    to=OWN,
                    ref_id="r",
                )
        assert send.await_count == 0

    async def test_a_closed_window_with_a_template_offers_the_file(self):
        from api.services.workflow import documents

        send = AsyncMock(
            return_value=SimpleNamespace(ok=True, message_id="wamid.o", error=None)
        )
        with (
            patch.object(
                documents, "own_channels", AsyncMock(return_value=({OWN}, set()))
            ),
            patch.object(
                documents,
                "download",
                AsyncMock(return_value=(b"%PDF", "a.pdf", "application/pdf")),
            ),
            patch(
                "api.services.messaging.platform_whatsapp.is_configured", lambda: True
            ),
            patch(
                "api.services.messaging.platform_whatsapp.credentials",
                lambda: {"access_token": "t", "phone_number_id": "p"},
            ),
            patch.object(wa, "session_open", AsyncMock(return_value=False)),
            patch.object(constants, "WHATSAPP_FILE_OFFER_TEMPLATE", "file_offer"),
            patch("api.services.messaging.send.send_message", send),
            patch.object(documents, "_charge_whatsapp", AsyncMock()),
        ):
            line = await documents.deliver(
                7, file_id="f", name="Rent.pdf", channel="whatsapp", to=OWN, ref_id="r"
            )
        assert line.startswith("Offered Rent.pdf")
        assert send.await_args.kwargs["template"] == {
            "name": "file_offer",
            "language": "en",
            "params": ["Rent.pdf"],
        }
