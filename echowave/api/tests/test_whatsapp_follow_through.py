"""WhatsApp after the call, on Decibyl's own sender, billed one line a message.

Three things had to be true and were not: the caller's number has to be found
where a phone call actually puts it, the message has to go out with no carrier
account of the customer's, and the account has to be charged for it once.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from api.db.models import CreditLedgerModel, OrganizationModel
from api.enums import CreditLedgerKind
from api.services.billing import messaging_charges
from api.services.messaging import follow_up, platform_whatsapp
from api.services.messaging.send import META_WHATSAPP, send_message
from api.services.workflow.dto import ReactFlowDTO, SmsNodeData
from api.services.workflow.workflow_graph import WorkflowGraph

META = {
    "access_token": "EAAtoken",
    "phone_number_id": "1234567890",
    "graph_version": "v21.0",
}


def _response(status: int, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=payload if payload is not None else {},
        request=httpx.Request("POST", "https://graph.facebook.com/x"),
    )


class TestTheCallersNumberIsFoundWhereACallPutsIt:
    def test_top_level_still_wins(self):
        node = SmsNodeData(name="c", body="hi")
        assert (
            follow_up.resolve_recipient(node, {"phone_number": "+919876543210"})
            == "+919876543210"
        )

    def test_a_phone_call_nests_it_under_the_run_context(self):
        """The post-call render context keeps initial_context and
        gathered_context as sections; before this the number was never found
        and every real call texted nobody."""
        node = SmsNodeData(name="c", body="hi")
        variables = {
            "workflow_run_id": 1,
            "initial_context": {"phone_number": "+919000000001"},
            "gathered_context": {},
        }
        assert follow_up.resolve_recipient(node, variables) == "+919000000001"

    def test_what_the_agent_gathered_outranks_what_the_trigger_sent(self):
        node = SmsNodeData(name="c", body="hi")
        variables = {
            "initial_context": {"phone_number": "+919000000001"},
            "gathered_context": {"mobile": "+919000000002"},
        }
        assert follow_up.resolve_recipient(node, variables) == "+919000000002"


class TestTheMetaSender:
    async def test_a_template_goes_out_as_meta_expects(self):
        post = AsyncMock(return_value=_response(200, {"messages": [{"id": "wamid.1"}]}))
        with patch("httpx.AsyncClient.post", post):
            result = await send_message(
                provider=META_WHATSAPP,
                credentials=META,
                to="+91 98765 43210",
                from_="",
                body="fallback",
                template={
                    "name": "booking_confirmed",
                    "language": "en",
                    "params": ["Ravi", "5:30 pm"],
                },
            )
        assert result.ok and result.message_id == "wamid.1"
        url = post.await_args.args[0]
        assert url.endswith("/v21.0/1234567890/messages")
        payload = post.await_args.kwargs["json"]
        assert payload["to"] == "919876543210"
        assert payload["type"] == "template"
        assert payload["template"]["name"] == "booking_confirmed"
        texts = [p["text"] for p in payload["template"]["components"][0]["parameters"]]
        assert texts == ["Ravi", "5:30 pm"]
        assert post.await_args.kwargs["headers"]["Authorization"] == "Bearer EAAtoken"

    async def test_no_template_sends_the_text_as_written(self):
        post = AsyncMock(return_value=_response(200, {"messages": [{"id": "wamid.2"}]}))
        with patch("httpx.AsyncClient.post", post):
            await send_message(
                provider=META_WHATSAPP,
                credentials=META,
                to="+919876543210",
                from_="",
                body="Your quote is on its way.",
            )
        payload = post.await_args.kwargs["json"]
        assert payload["type"] == "text"
        assert payload["text"]["body"] == "Your quote is on its way."

    async def test_no_sender_number_is_needed(self):
        """The sender is the business phone number id; a customer has none."""
        post = AsyncMock(return_value=_response(200, {"messages": [{"id": "wamid.3"}]}))
        with patch("httpx.AsyncClient.post", post):
            result = await send_message(
                provider=META_WHATSAPP,
                credentials=META,
                to="+919876543210",
                from_="",
                body="hi",
            )
        assert result.ok

    async def test_metas_refusal_carries_its_own_words(self):
        post = AsyncMock(
            return_value=_response(
                400,
                {
                    "error": {
                        "message": "(#131047) Re-engagement message",
                        "code": 131047,
                    }
                },
            )
        )
        with patch("httpx.AsyncClient.post", post):
            result = await send_message(
                provider=META_WHATSAPP,
                credentials=META,
                to="+919876543210",
                from_="",
                body="hi",
            )
        assert not result.ok
        assert "131047" in (result.error or "")


class TestTheNodeCarriesATemplate:
    def test_template_values_are_rendered_in_order(self):
        node = SmsNodeData(
            name="c",
            body="hi",
            channel="whatsapp",
            template_name="booking_confirmed",
            template_language="ta",
            template_params="{{gathered_context.patient_name}}, {{gathered_context.slot}}",
        )
        template = follow_up.resolve_template(
            node, {"gathered_context": {"patient_name": "Ravi", "slot": "Wed 5:30 pm"}}
        )
        assert template == {
            "name": "booking_confirmed",
            "language": "ta",
            "params": ["Ravi", "Wed 5:30 pm"],
        }

    def test_no_template_name_means_no_template(self):
        assert follow_up.resolve_template(SmsNodeData(name="c", body="hi"), {}) is None

    async def test_the_platform_provider_passes_the_template_through(self):
        node = SmsNodeData(
            name="c",
            body="hi",
            channel="whatsapp",
            template_name="t",
            template_params="x",
        )
        post = AsyncMock(return_value=_response(200, {"messages": [{"id": "wamid.9"}]}))
        with patch("httpx.AsyncClient.post", post):
            result = await follow_up.deliver(
                node,
                variables={"phone_number": "+919876543210"},
                provider=META_WHATSAPP,
                credentials=META,
            )
        assert result is not None and result.ok
        assert post.await_args.kwargs["json"]["type"] == "template"


class TestThePlatformSender:
    def test_absent_by_default(self, monkeypatch):
        from api import constants

        monkeypatch.setattr(constants, "WHATSAPP_ACCESS_TOKEN", "")
        monkeypatch.setattr(constants, "WHATSAPP_PHONE_NUMBER_ID", "")
        assert not platform_whatsapp.is_configured()

    def test_present_when_both_are_set(self, monkeypatch):
        from api import constants

        monkeypatch.setattr(constants, "WHATSAPP_ACCESS_TOKEN", "tok")
        monkeypatch.setattr(constants, "WHATSAPP_PHONE_NUMBER_ID", "123")
        assert platform_whatsapp.is_configured()
        assert platform_whatsapp.credentials()["phone_number_id"] == "123"


class TestOneLineAMessage:
    async def test_a_sent_message_is_charged_once(self, async_session):
        org = OrganizationModel(provider_id="org-wa-charge", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()

        first = await messaging_charges.debit_message(
            async_session,
            organization_id=org.id,
            message_id="wamid.abc",
            workflow_run_id=7,
            node_name="Confirm",
        )
        again = await messaging_charges.debit_message(
            async_session,
            organization_id=org.id,
            message_id="wamid.abc",
            workflow_run_id=7,
            node_name="Confirm",
        )
        assert first == messaging_charges.price_paise() == 100
        assert again == 0
        rows = (
            (
                await async_session.execute(
                    __import__("sqlalchemy")
                    .select(CreditLedgerModel)
                    .where(CreditLedgerModel.organization_id == org.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].kind == CreditLedgerKind.MESSAGE.value
        assert rows[0].delta_paise == -100
        assert "vendor cost 12 paise" in (rows[0].note or "")

    async def test_a_message_with_no_id_is_not_charged(self, async_session):
        assert (
            await messaging_charges.debit_message(
                async_session, organization_id=1, message_id=""
            )
            == 0
        )


class TestTheDemosCarryTheStep:
    @pytest.mark.parametrize("module", ["narayani_dental", "logicorp_quotes"])
    def test_a_whatsapp_step_is_in_the_definition_and_the_graph_accepts_it(
        self, module
    ):
        import importlib

        demo = importlib.import_module(f"scripts.demos.{module}")
        graph = demo.definition({})
        steps = [n for n in graph["nodes"] if n["type"] == "sms"]
        assert len(steps) == 1
        assert steps[0]["data"]["channel"] == "whatsapp"
        assert steps[0]["data"]["body"].strip()
        WorkflowGraph(ReactFlowDTO.model_validate(graph))
