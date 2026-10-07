"""Outreach drafts: one card per lead, exactly what is sent, nothing sent yet.

``draft_outreach`` turns written emails into ordinary ``run_tool`` send
cards on the person's own connected mailbox, so Confirm, Confirm all, the
undo window, the version binding and the prospect stamps are the ones every
other send uses. What this file holds it to:

* one card per lead, each showing exactly the recipient, subject and body;
* no card at all without a mailbox -- a connect card and the drafts back to
  the model instead, never a card that cannot run;
* nobody unsubscribed, bounced or declined is written to, and somebody
  already written to only as a follow-up;
* junk addresses and duplicates are named, not dropped silently.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel
from api.enums import AgentEventKind, ToolCategory
from api.services.outreach import tools as outreach
from api.services.workflow import actions


def _gmail() -> SimpleNamespace:
    return SimpleNamespace(
        tool_uuid="uuid-gmail-send",
        name="Gmail — Send Email",
        category=ToolCategory.COMPOSIO.value,
        definition={
            "type": "composio",
            "config": {"tool_slug": "GMAIL_SEND_EMAIL", "toolkit": "gmail"},
        },
    )


async def _org() -> int:
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"drafts-{uuid4().hex}")
        session.add(org)
        await session.flush()
        organization_id = int(org.id)
        await session.commit()
    return organization_id


@pytest.fixture
async def org(test_engine, monkeypatch):
    monkeypatch.setattr(constants, "OUTREACH_ENABLED", True)
    organization_id = await _org()
    yield organization_id
    async with db_client.async_session() as session:
        for table in ("agent_events", "contacts", "contact_lists"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"),
                {"o": organization_id},
            )
        await session.commit()


def _mailbox(connected: bool = True):
    tools = [_gmail()] if connected else []
    return (
        patch(
            "api.services.workflow.connected_tools.list_for_organization",
            AsyncMock(return_value=tools),
        ),
        patch(
            "api.services.workflow.connected_tools.is_connected",
            return_value=True,
        ),
        patch.object(
            actions.db_client, "get_tool_by_uuid", AsyncMock(return_value=_gmail())
        ),
    )


EMAILS = [
    {
        "to": "asha@lotusdental.example.com",
        "name": "Asha Rao",
        "company": "Lotus Dental",
        "subject": "Missed calls at Lotus Dental",
        "body": "Hi Asha,\n\nYour second clinic in Pune must be busy.\n\nRavi",
        "why": "Owner of a two-clinic practice in Pune",
    },
    {
        "to": "vikram@smilestudio.example.com",
        "name": "Vikram Shah",
        "company": "Smile Studio",
        "subject": "Front desk at Smile Studio",
        "body": "Hi Vikram,\n\nA short note about evening calls.\n\nRavi",
    },
]


async def _cards(organization_id: int) -> list:
    return await db_client.agent_events(
        organization_id=organization_id,
        kinds=[AgentEventKind.ACTION_PROPOSED.value],
        assistant_thread=True,
    )


@pytest.mark.asyncio
class TestOneCardPerLead:
    async def test_each_card_shows_exactly_what_is_sent(self, org):
        a, b, c = _mailbox()
        with a, b, c:
            out = await outreach.draft(org, {"emails": EMAILS})
        assert out["status"] == "proposed" and out["cards"] == 2
        cards = await _cards(org)
        assert len(cards) == 2
        previews = {
            c.payload["args"]["arguments"]["recipient_email"]: c.payload["preview"]
            for c in cards
        }
        for email in EMAILS:
            preview = previews[email["to"]]
            assert f"To: {email['to']}" in preview
            assert f"Subject: {email['subject']}" in preview
            assert email["body"] in preview
        for card in cards:
            assert card.payload["state"] == actions.PROPOSED
            assert card.payload["action"] == actions.RUN_TOOL
            assert card.payload["reaches_people"] is True
        assert any("Owner of a two-clinic" in c.payload["why"] for c in cards)

    async def test_nothing_is_sent_by_drafting(self, org):
        execute = AsyncMock()
        a, b, c = _mailbox()
        with a, b, c, patch("api.services.workflow.connected_tools.execute", execute):
            await outreach.draft(org, {"emails": EMAILS})
        execute.assert_not_awaited()


@pytest.mark.asyncio
class TestNoMailbox:
    async def test_a_connect_card_and_the_drafts_back_not_dead_cards(self, org):
        offer = AsyncMock(
            return_value={
                "status": "offered",
                "note": "A connect card for Gmail is on the thread.",
            }
        )
        a, b, c = _mailbox(connected=False)
        with a, b, c, patch("api.services.workflow.connector_offer.offer", offer):
            out = await outreach.draft(org, {"emails": EMAILS})
        assert out["status"] == "needs_connection"
        assert [d["to"] for d in out["drafts"]] == [e["to"] for e in EMAILS]
        assert offer.await_args.kwargs["arguments"]["app"] == "gmail"
        assert await _cards(org) == []


@pytest.mark.asyncio
class TestWhoIsNotWrittenTo:
    async def _prospect(self, org, email: str, **attributes) -> None:
        listed = await db_client.create_contact_list(
            organization_id=org, name=f"Prospects {uuid4().hex[:6]}"
        )
        await db_client.upsert_contacts(
            listed.id,
            organization_id=org,
            rows=[{"email": email, "name": "x", "attributes": attributes}],
        )

    async def test_unsubscribed_and_already_emailed_are_skipped_and_named(self, org):
        await self._prospect(org, EMAILS[0]["to"], status="unsubscribed")
        await self._prospect(org, EMAILS[1]["to"], emails_sent=1)
        a, b, c = _mailbox()
        with a, b, c:
            out = await outreach.draft(org, {"emails": EMAILS})
        assert out["status"] == "nothing_to_send"
        reasons = {s["to"]: s["reason"] for s in out["skipped"]}
        assert "unsubscribed" in reasons[EMAILS[0]["to"]]
        assert "follow-up" in reasons[EMAILS[1]["to"]]
        assert await _cards(org) == []

    async def test_a_follow_up_to_somebody_written_to_is_a_card(self, org):
        await self._prospect(org, EMAILS[1]["to"], emails_sent=1)
        a, b, c = _mailbox()
        with a, b, c:
            out = await outreach.draft(
                org, {"emails": [{**EMAILS[1], "follow_up": True}]}
            )
        assert out["cards"] == 1
        assert (await _cards(org))[0].payload["why"].startswith("Follow-up")

    async def test_junk_and_duplicates_are_named(self, org):
        a, b, c = _mailbox()
        with a, b, c:
            out = await outreach.draft(
                org,
                {
                    "emails": [
                        EMAILS[0],
                        EMAILS[0],
                        {**EMAILS[1], "to": "not-an-address"},
                        {**EMAILS[1], "subject": ""},
                    ]
                },
            )
        assert out["cards"] == 1
        reasons = [s["reason"] for s in out["skipped"]]
        assert "listed twice" in reasons
        assert "not an email address" in reasons
        assert "needs a subject and a body" in reasons

    async def test_too_many_at_once_is_refused(self, org):
        out = await outreach.draft(
            org, {"emails": [EMAILS[0]] * (outreach.MAX_DRAFTS + 1)}
        )
        assert out["status"] == "error"
