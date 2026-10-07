"""The outreach agent, end to end in Chat, with the model stubbed.

A business owner says what they sell and who to; Decibyl finds leads,
saves them, and drafts one email each; the owner confirms all; each email
goes from their own mailbox and the prospect is stamped; a follow-up is a
new card. The model is the only thing scripted (no key here): the turn
loop, the tools, the cards, the prospects and the database are the real
ones. Apollo and the mailbox are fakes at their HTTP and app edges.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel
from api.enums import AgentEventKind, CostComponent, ToolCategory
from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.configuration import organization_credentials
from api.services.outreach import leads
from api.services.workflow import actions, decibyl

GMAIL = SimpleNamespace(
    tool_uuid="uuid-gmail-send",
    name="Gmail — Send Email",
    description="Send an email from the connected Gmail account.",
    category=ToolCategory.COMPOSIO.value,
    definition={
        "type": "composio",
        "config": {"tool_slug": "GMAIL_SEND_EMAIL", "toolkit": "gmail"},
    },
)

PEOPLE = [
    {
        "id": "p1",
        "first_name": "Asha",
        "title": "Owner",
        "organization": {"name": "Lotus Dental"},
    },
    {
        "id": "p2",
        "first_name": "Vikram",
        "title": "Practice Manager",
        "organization": {"name": "Smile Studio"},
    },
]
MATCHES = [
    {
        "id": "p1",
        "name": "Asha Rao",
        "title": "Owner",
        "email": "asha@lotusdental.example.com",
        "email_status": "verified",
        "city": "Pune",
        "organization": {
            "name": "Lotus Dental",
            "website_url": "http://lotusdental.example.com",
        },
    },
    {
        "id": "p2",
        "name": "Vikram Shah",
        "title": "Practice Manager",
        "email": "vikram@smilestudio.example.com",
        "email_status": "verified",
        "city": "Pune",
        "organization": {"name": "Smile Studio"},
    },
]


def _apollo():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/mixed_people/api_search"):
            return httpx.Response(200, json={"people": PEOPLE, "total_entries": 40})
        return httpx.Response(200, json={"matches": MATCHES})

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return patch.object(leads.httpx, "AsyncClient", client)


def _email(lead: dict, follow_up: bool = False) -> dict:
    first = lead["name"].split()[0]
    return {
        "to": lead["email"],
        "name": lead["name"],
        "company": lead["organization"]["name"],
        "subject": ("Re: " if follow_up else "")
        + f"Evening calls at {lead['organization']['name']}",
        "body": f"Hi {first},\n\nClinics in Pune lose evening calls. We answer them.\n\nRavi",
        "why": f"{lead['title']} at a Pune dental clinic",
        "follow_up": follow_up,
    }


def _call(i: int, name: str, arguments: dict) -> ModelReply:
    return ModelReply(
        text="", tool_calls=(ToolCall(id=f"c{i}", name=name, arguments=arguments),)
    )


async def _org() -> int:
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"journey-{uuid4().hex}")
        session.add(org)
        await session.flush()
        organization_id = int(org.id)
        await session.commit()
    return organization_id


@pytest.fixture
async def org(test_engine, monkeypatch):
    for flag in (
        "OUTREACH_ENABLED",
        "DECIBYL_TOOLS_2026_09_ENABLED",
        "TASK_LEDGER_ENABLED",
    ):
        monkeypatch.setattr(constants, flag, True)
    organization_id = await _org()
    async with db_client.async_session() as session:
        await organization_credentials.set_credential(
            session,
            organization_id=organization_id,
            actor_user_id=None,
            component=CostComponent.DATA,
            provider="apollo",
            api_key="own-apollo-key",
        )
        await session.commit()
    yield organization_id
    async with db_client.async_session() as session:
        for table in (
            "agent_task_transitions",
            "agent_tasks",
            "agent_events",
            "contacts",
            "contact_lists",
            "organization_provider_credentials",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"),
                {"o": organization_id},
            )
        await session.commit()


def _turn(replies: list[ModelReply]):
    """decibyl.answer with the model scripted and the context kept small;
    the tools, cards and database are real."""
    model = AsyncMock(side_effect=replies)
    return model, (
        patch.object(decibyl, "build_context", new=AsyncMock(return_value="## Team")),
        patch.object(decibyl, "office_context", new=AsyncMock(return_value="")),
        patch(
            "api.services.agent_builder.settings.resolve_model",
            new=AsyncMock(
                return_value=SimpleNamespace(provider="openai", model="m", api_key="k")
            ),
        ),
        patch("api.services.agent_builder.client.stream", new=model),
        patch(
            "api.services.workflow.connected_tools.list_for_organization",
            new=AsyncMock(return_value=[GMAIL]),
        ),
        patch("api.services.workflow.connected_tools.is_connected", return_value=True),
        patch.object(
            actions.db_client, "get_tool_by_uuid", new=AsyncMock(return_value=GMAIL)
        ),
        patch(
            "api.services.billing.lookup_source.charge",
            new=AsyncMock(return_value={"charged": True}),
        ),
        patch.object(actions.approvals, "check", new=AsyncMock()),
        patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
        _apollo(),
    )


def _tool_results(model: AsyncMock) -> list[dict]:
    """What the tools handed back to the model, in order."""
    conversation = model.await_args_list[-1].kwargs["conversation"]
    out = []
    for m in conversation.messages:
        if m.get("role") == "tool":
            content = m["content"]
            out.append(json.loads(content) if isinstance(content, str) else content)
    return out


async def _cards(org: int) -> list:
    return await db_client.agent_events(
        organization_id=org,
        kinds=[AgentEventKind.ACTION_PROPOSED.value],
        assistant_thread=True,
    )


@pytest.mark.asyncio
async def test_find_draft_confirm_all_send_and_follow_up(org):
    # 1. "Find me customers" -> find_leads, then save and draft, then say so.
    replies = [
        _call(
            1,
            "find_leads",
            {
                "titles": ["Clinic owner", "Practice manager"],
                "locations": ["Pune"],
                "industries": ["dental clinics"],
                "limit": 5,
            },
        ),
        _call(
            2,
            "save_prospects",
            {
                "prospects": [
                    {
                        "name": m["name"],
                        "email": m["email"],
                        "company": m["organization"]["name"],
                        "title": m["title"],
                        "source_url": "https://www.apollo.io",
                        "note": "Pune dental clinic",
                    }
                    for m in MATCHES
                ]
            },
        ),
        _call(3, "draft_outreach", {"emails": [_email(m) for m in MATCHES]}),
        ModelReply(
            text="I found 2 dental clinics in Pune and put 2 emails on cards for you to confirm."
        ),
    ]
    model, patches = _turn(replies)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        said = await decibyl.answer(
            org,
            "We answer calls for dental clinics. Find me customers in Pune and write to them.",
        )
    assert "2 emails" in said
    # The model saw real leads from the provider, not an empty list.
    found = _tool_results(model)[0]
    assert [lead["name"] for lead in found["leads"]] == ["Asha Rao", "Vikram Shah"]

    cards = await _cards(org)
    assert len(cards) == 2
    for card in cards:
        assert card.payload["state"] == actions.PROPOSED
        assert card.payload["preview"].startswith("To: ")

    # 2. Confirm all: each card against the version it showed.
    with (
        patch.object(actions.approvals, "check", new=AsyncMock()),
        patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
    ):
        results = await actions.settle_many(
            organization_id=org,
            items=[{"event_id": c.id, "version": c.payload["version"]} for c in cards],
            user_id=1,
        )
    assert all(r["ok"] for r in results)

    # 3. The window passes; each email goes from the owner's own mailbox.
    sent = AsyncMock(return_value={"status": "success", "data": {"id": "m1"}})
    with (
        patch("api.services.workflow.connected_tools.execute", sent),
        patch("api.services.workflow.connected_tools.is_connected", return_value=True),
        patch.object(
            actions.db_client, "get_tool_by_uuid", new=AsyncMock(return_value=GMAIL)
        ),
    ):
        for card in cards:
            await actions.run(card.id, org)
    assert sent.await_count == 2
    recipients = sorted(
        c.kwargs["arguments"]["recipient_email"] for c in sent.await_args_list
    )
    assert recipients == sorted(m["email"] for m in MATCHES)
    for card in await _cards(org):
        assert card.payload["state"] == actions.DONE
    rows = await db_client.search_contacts_for_organization(
        org, [MATCHES[0]["email"]], limit=3
    )
    assert rows and int(rows[0].attributes["emails_sent"]) == 1

    # 4. A first email again is refused; a follow-up is a new card.
    replies = [
        _call(4, "draft_outreach", {"emails": [_email(MATCHES[0])]}),
        _call(5, "draft_outreach", {"emails": [_email(MATCHES[0], follow_up=True)]}),
        ModelReply(
            text="Asha has not replied, so I drafted one follow-up for you to confirm."
        ),
    ]
    model, patches = _turn(replies)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await decibyl.answer(org, "Follow up with anyone who has not replied.")
    refused = _tool_results(model)[0]
    assert refused["status"] == "nothing_to_send"
    waiting = [c for c in await _cards(org) if c.payload["state"] == actions.PROPOSED]
    assert len(waiting) == 1
    assert waiting[0].payload["why"].startswith("Follow-up")
    assert "Re: Evening calls" in waiting[0].payload["preview"]
