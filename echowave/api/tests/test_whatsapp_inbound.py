"""The platform WhatsApp number hears (A3 groundwork): only Meta may post,
only a verified number is anybody's, a file becomes a document, twice is
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


@pytest.mark.asyncio
class TestRouting:
    def _inbound(self, kind="text", text="where is my aadhaar", mid="wamid.1"):
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

    async def test_a_verified_number_reaches_its_accounts_decibyl_and_replies_there(
        self,
    ):
        ask = AsyncMock(return_value=[])
        with (
            patch.object(wa.db_client, "find_verified_number_owner", create=True),
            patch.object(
                wa.db_client,
                "find_organization_by_verified_number",
                AsyncMock(return_value=7),
            ),
            patch.object(wa, "seen_before", AsyncMock(return_value=False)),
            patch.object(wa, "touch_session", AsyncMock()) as touch,
            patch.object(
                wa.db_client,
                "get_organization_users",
                AsyncMock(return_value=[SimpleNamespace(id=3, provider_id="u3")]),
            ),
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert await wa.handle(self._inbound()) == "accepted"
        touch.assert_awaited_once_with(OWN)
        kwargs = ask.await_args.kwargs
        assert kwargs["organization_id"] == 7 and kwargs["user_id"] == 3
        assert kwargs["text"] == "where is my aadhaar"
        assert kwargs["reply_to"] == {"channel": "whatsapp", "to": OWN}

    async def test_an_unverified_number_is_nobodys_and_is_dropped(self):
        ask = AsyncMock()
        with (
            patch.object(
                wa.db_client,
                "find_organization_by_verified_number",
                AsyncMock(return_value=None),
            ),
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert await wa.handle(self._inbound()) == "unknown_number"
        assert ask.await_count == 0

    async def test_a_redelivery_answers_nothing_twice(self):
        ask = AsyncMock()
        with (
            patch.object(
                wa.db_client,
                "find_organization_by_verified_number",
                AsyncMock(return_value=7),
            ),
            patch.object(wa, "seen_before", AsyncMock(return_value=True)),
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert await wa.handle(self._inbound()) == "duplicate"
        assert ask.await_count == 0

    async def test_a_document_is_filed_and_reaches_decibyl_as_an_attachment(self):
        ask = AsyncMock(return_value=[])
        create = AsyncMock(return_value=SimpleNamespace(id=55))
        enqueue = AsyncMock()
        with (
            patch.object(
                wa.db_client,
                "find_organization_by_verified_number",
                AsyncMock(return_value=7),
            ),
            patch.object(wa, "seen_before", AsyncMock(return_value=False)),
            patch.object(wa, "touch_session", AsyncMock()),
            patch.object(
                wa.db_client,
                "get_organization_users",
                AsyncMock(return_value=[SimpleNamespace(id=3, provider_id="u3")]),
            ),
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
                await wa.handle(self._inbound(kind="document", text="my policy"))
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
            patch.object(
                wa.db_client,
                "find_organization_by_verified_number",
                AsyncMock(return_value=7),
            ),
            patch.object(wa, "seen_before", AsyncMock(return_value=False)),
            patch.object(wa, "touch_session", AsyncMock()),
            patch.object(
                wa.db_client,
                "get_organization_users",
                AsyncMock(return_value=[SimpleNamespace(id=3, provider_id="u3")]),
            ),
            patch.object(
                wa, "download_media", AsyncMock(side_effect=ValueError("gone"))
            ),
            patch("api.services.workflow.decibyl.ask", ask),
        ):
            assert await wa.handle(self._inbound(kind="image", text="")) == "accepted"
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
