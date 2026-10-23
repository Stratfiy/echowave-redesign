"""A payment an agent asked for is confirmed back in the conversation.

Real: the connected-app executor's success path, the run the link was made
in and what it says about the customer, the stored link, the watch's choice
of what to check, and the statuses it files. Stubbed: Composio's servers
(the HTTP post, the catalogue and the action schema), Instagram's send, and
the timeline write, each checked for what it was handed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pipecat.utils.run_context import get_current_run_id, set_current_run_id

from api import constants
from api.db import db_client
from api.services.integrations.composio import client as composio
from api.services.workflow import payment_links
from api.tests.support.voice import all_on, clean, make_people

DEFINITION = {
    "nodes": [
        {
            "id": "s",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {"name": "Answer", "prompt": "Take payments."},
        }
    ],
    "edges": [],
}


@pytest.fixture
async def people(test_engine, monkeypatch):
    all_on(monkeypatch)
    monkeypatch.setattr(constants, "PAYMENT_CONFIRMATIONS_ENABLED", True)
    monkeypatch.setattr(composio, "COMPOSIO_API_KEY", "test-key")
    p = await make_people(f"pay-{uuid4().hex[:6]}")
    p.agent = await db_client.create_workflow(
        name="Billing desk",
        workflow_definition=DEFINITION,
        user_id=p.a.id,
        organization_id=p.org,
    )
    yield p
    await clean(p)


async def _run(people, **context):
    return await db_client.create_workflow_run(
        name="Instagram - today",
        workflow_id=people.agent.id,
        mode="textchat",
        user_id=people.a.id,
        organization_id=people.org,
        initial_context=context,
    )


def _composio_reply(data: dict):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"successful": True, "data": data}
    return response


LINK = {
    "id": "plink_ABC123",
    "short_url": "https://rzp.io/i/abc",
    "amount": 50000,
    "currency": "INR",
    "description": "Teeth cleaning",
    "status": "created",
}


async def _create_link(people, run_id: int | None, link: dict = LINK):
    set_current_run_id(run_id)
    try:
        with patch(
            "httpx.AsyncClient.post", AsyncMock(return_value=_composio_reply(link))
        ):
            return await composio.execute_tool(
                tool_slug=payment_links.CREATE_SLUG,
                arguments={"amount": 50000, "description": "Teeth cleaning"},
                organization_id=people.org,
                connected_account_id="ca_1",
            )
    finally:
        set_current_run_id(None)


async def _rows(people):
    return await db_client.payment_links_for_organization(people.org)


@pytest.mark.asyncio
class TestRecorded:
    async def test_a_link_made_in_a_conversation_is_kept_with_it(self, people):
        run = await _run(people, channel="instagram", instagram_user_id="C77")
        result = await _create_link(people, run.id)
        assert result["status"] == "success"
        [row] = await _rows(people)
        assert row.link_id == "plink_ABC123"
        assert row.amount_minor == 50000
        assert row.short_url == "https://rzp.io/i/abc"
        assert (row.workflow_run_id, row.workflow_id) == (run.id, people.agent.id)
        assert (row.channel, row.recipient) == ("instagram", "C77")
        assert row.connected_account_id == "ca_1"
        assert row.status == "created"

    async def test_on_a_call_the_customer_is_the_number_on_the_line(self, people):
        run = await _run(people, phone_number="+919876543210", provider="twilio")
        await _create_link(people, run.id)
        [row] = await _rows(people)
        assert row.recipient == "+919876543210"

    async def test_the_same_link_twice_is_kept_once(self, people):
        await _create_link(people, None)
        await _create_link(people, None)
        assert len(await _rows(people)) == 1

    async def test_switched_off_nothing_is_kept(self, people, monkeypatch):
        monkeypatch.setattr(constants, "PAYMENT_CONFIRMATIONS_ENABLED", False)
        await _create_link(people, None)
        assert await _rows(people) == []


@pytest.mark.asyncio
class TestTheActionIsFoundNotGuessed:
    async def test_from_the_catalogue_and_the_schema(self):
        with (
            patch(
                "api.services.integrations.composio.client.toolkit_actions",
                AsyncMock(
                    return_value=[
                        {"slug": "RAZORPAY_FETCH_ALL_PAYMENT_LINKS"},
                        {"slug": "RAZORPAY_FETCH_PAYMENT_LINK"},
                    ]
                ),
            ),
            patch(
                "api.services.integrations.composio.schema.input_schema",
                AsyncMock(
                    return_value={
                        "properties": {"expand": {}, "payment_link_id": {}},
                        "required": ["payment_link_id"],
                    }
                ),
            ),
        ):
            assert await payment_links.fetch_action(1) == (
                "RAZORPAY_FETCH_PAYMENT_LINK",
                "payment_link_id",
            )

    async def test_none_in_the_catalogue_is_none(self):
        with patch(
            "api.services.integrations.composio.client.toolkit_actions",
            AsyncMock(return_value=[{"slug": "RAZORPAY_CREATE_PAYMENT_LINK"}]),
        ):
            assert await payment_links.fetch_action(1) is None


ACTION = ("RAZORPAY_FETCH_PAYMENT_LINK", "payment_link_id")


@pytest.mark.asyncio
class TestWatched:
    async def _row(self, people, **context):
        run = await _run(people, **context) if context else None
        await _create_link(people, run.id if run else None)
        [row] = await _rows(people)
        return row

    async def test_paid_on_instagram_the_owner_is_told_and_the_customer_thanked(
        self, people
    ):
        row = await self._row(people, channel="instagram", instagram_user_id="C77")
        record, send = AsyncMock(), AsyncMock(return_value=["m1"])
        fetched = AsyncMock(
            return_value={"status": "success", "data": {**LINK, "status": "paid"}}
        )
        with (
            patch("api.services.integrations.composio.client.execute_tool", fetched),
            patch("api.services.workflow.agent_timeline.record", record),
            patch("api.services.messaging.instagram.send_text", send),
            patch.object(
                db_client,
                "get_instagram_account",
                AsyncMock(return_value=SimpleNamespace(id=1)),
            ),
        ):
            assert await payment_links.check(row, ACTION) == "paid"
        assert fetched.await_args.kwargs["arguments"] == {
            "payment_link_id": "plink_ABC123"
        }
        assert fetched.await_args.kwargs["connected_account_id"] == "ca_1"
        assert send.await_args.kwargs["to"] == "C77"
        assert "₹500 received" in send.await_args.kwargs["text"]
        summary = record.await_args.kwargs["summary"]
        assert "₹500 received for Teeth cleaning" in summary
        assert "thanked" in summary
        assert record.await_args.kwargs["workflow_id"] == people.agent.id
        [stored] = await _rows(people)
        assert stored.status == "paid" and stored.paid_at is not None

    async def test_paid_on_a_call_the_owner_is_told_the_customer_was_not_messaged(
        self, people
    ):
        row = await self._row(people, phone_number="+919876543210")
        record = AsyncMock()
        with (
            patch(
                "api.services.integrations.composio.client.execute_tool",
                AsyncMock(
                    return_value={"status": "success", "data": {"status": "paid"}}
                ),
            ),
            patch("api.services.workflow.agent_timeline.record", record),
        ):
            assert await payment_links.check(row, ACTION) == "paid"
        assert "was not messaged" in record.await_args.kwargs["summary"]

    async def test_not_paid_yet_is_checked_again_later(self, people):
        row = await self._row(people)
        with patch(
            "api.services.integrations.composio.client.execute_tool",
            AsyncMock(
                return_value={"status": "success", "data": {"status": "created"}}
            ),
        ):
            assert await payment_links.check(row, ACTION) == "created"
        [stored] = await _rows(people)
        assert stored.status == "created" and stored.checked_at is not None

    async def test_cannot_be_checked_says_so_once(self, people):
        row = await self._row(people)
        record = AsyncMock()
        with patch("api.services.workflow.agent_timeline.record", record):
            assert await payment_links.check(row, None) == payment_links.UNVERIFIABLE
        assert (
            "cannot be confirmed automatically" in record.await_args.kwargs["summary"]
        )
        [stored] = await _rows(people)
        assert stored.status == payment_links.UNVERIFIABLE

    async def test_a_pass_checks_what_is_due_and_stops_watching_old_links(self, people):
        await self._row(people)
        old = await db_client.record_payment_link(
            organization_id=people.org,
            link_id="plink_OLD",
            amount_minor=100,
        )
        await db_client.update_payment_link(
            old.id,
            organization_id=people.org,
            created_at=datetime.now(UTC) - timedelta(days=4),
        )
        with (
            patch.object(payment_links, "fetch_action", AsyncMock(return_value=ACTION)),
            patch(
                "api.services.integrations.composio.client.execute_tool",
                AsyncMock(
                    return_value={"status": "success", "data": {"status": "created"}}
                ),
            ),
        ):
            counts = await payment_links.watch()
        assert counts["stopped_watching"] >= 1
        statuses = {r.link_id: r.status for r in await _rows(people)}
        assert statuses == {"plink_ABC123": "created", "plink_OLD": "expired"}


class TestWords:
    def test_money(self):
        assert payment_links.money(50000, "INR") == "₹500"
        assert payment_links.money(12345, "INR") == "₹123.45"
        assert payment_links.money(1000, "usd") == "USD 10"


@pytest.mark.asyncio
async def test_a_chat_turn_names_its_run_and_puts_it_back(monkeypatch):
    from api.services.workflow import text_chat_session_service as service

    seen = {}

    async def turn(**kwargs):
        seen["run"] = get_current_run_id()
        raise RuntimeError("model down")

    monkeypatch.setattr(service, "execute_text_chat_pending_turn", turn)
    monkeypatch.setattr(service, "_mark_pending_turn_failed", AsyncMock())
    set_current_run_id("outer")
    try:
        with pytest.raises(service.TextChatSessionExecutionError):
            await service.execute_pending_text_chat_turn(
                workflow_id=1,
                run_id=42,
                text_session=SimpleNamespace(session_data={}, checkpoint={}),
            )
        assert seen["run"] == "42"
        assert get_current_run_id() == "outer"
    finally:
        set_current_run_id(None)
