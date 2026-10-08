"""Outreach from an uploaded list, and from a pasted business link.

The model is scripted (no key here); everything it touches is real: the
attachment's text in the turn, the table tools reading the original Excel
file, the Prospects list, the send cards and the database.

* **A list.** The owner attaches their lead list as Excel and asks which to
  write to. Decibyl ranks every row by rules drawn from what the owner
  sells, says why for each it picks, saves those, and drafts one card each
  -- and none for the row that does not fit.
* **A link.** The owner pastes their website. Decibyl reads it and asks the
  lead provider -- which prices the search first and spends nothing until
  the owner agrees.
"""

from __future__ import annotations

import csv
import io
import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel
from api.enums import AgentEventKind, CostComponent, ToolCategory
from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.configuration import organization_credentials
from api.services.knowledge_base import extraction
from api.services.workflow import actions, decibyl, tables

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

LIST = """Name,Title,Company,Email,City,Employees,Notes
Asha Rao,Owner,Lotus Dental Care,asha.rao@lotusdental.example.com,Pune,18,Two clinics; hiring a receptionist
Vikram Shah,Practice Manager,Smile Studio,vikram@smilestudio.example.org,Mumbai,7,Evening clinic
Neha Iyer,Founder,Bright Teeth Kids,neha@brightteeth.example.net,Pune,12,Paediatric dental chain
Rahul Mehta,Accountant,Mehta & Co,rahul@mehtaco.example.com,Delhi,4,Accounting firm
"""


def _xlsx(csv_text: str) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Leads"
    for row in csv.reader(io.StringIO(csv_text)):
        sheet.append([int(c) if c.isdigit() else c for c in row])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _text_of(data: bytes) -> str:
    return "\n".join(
        extraction._blocks_from_csv(t, delimiter=",")[i].text
        for _title, t in extraction.xlsx_sheets_as_csv(data)
        for i in range(len(extraction._blocks_from_csv(t, delimiter=",")))
    )


async def _org() -> int:
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"list-{uuid4().hex}")
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
        "TABLE_TOOLS_ENABLED",
    ):
        monkeypatch.setattr(constants, flag, True)
    monkeypatch.setattr(constants, "LEAD_DATA_PROVIDER", "treg")
    organization_id = await _org()
    yield organization_id
    async with db_client.async_session() as session:
        for table in (
            "agent_task_transitions",
            "agent_tasks",
            "agent_events",
            "contacts",
            "contact_lists",
            "knowledge_base_documents",
            "organization_provider_credentials",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"),
                {"o": organization_id},
            )
        await session.commit()


async def _attach(org: int, data: bytes, filename: str) -> str:
    user, _ = await db_client.get_or_create_user_by_provider_id(
        f"owner-{uuid4().hex[:8]}"
    )
    doc = await db_client.create_document(
        organization_id=org,
        created_by=user.id,
        filename=filename,
        file_size_bytes=len(data),
        file_hash=uuid4().hex,
        mime_type="application/octet-stream",
        scope="org",
    )
    async with db_client.async_session() as session:
        await session.execute(
            text(
                "UPDATE knowledge_base_documents SET processing_status = 'completed', "
                "full_text = :t WHERE document_uuid = :u"
            ),
            {"t": _text_of(data), "u": str(doc.document_uuid)},
        )
        await session.commit()
    return str(doc.document_uuid)


def _call(i: int, name: str, arguments: dict) -> ModelReply:
    return ModelReply(
        text="", tool_calls=(ToolCall(id=f"c{i}", name=name, arguments=arguments),)
    )


def _turn(replies, extra=()):
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
        patch.object(actions.approvals, "check", new=AsyncMock()),
        patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
        *extra,
    )


def _results(model: AsyncMock) -> list[dict]:
    conversation = model.await_args_list[-1].kwargs["conversation"]
    out = []
    for m in conversation.messages:
        if m.get("role") == "tool":
            c = m["content"]
            out.append(json.loads(c) if isinstance(c, str) else c)
    return out


def _seen_by_model(model: AsyncMock) -> str:
    first = model.await_args_list[0].kwargs
    return json.dumps(
        [first.get("system"), first["conversation"].messages], default=str
    )


RULES = [
    {
        "column": "Notes",
        "op": "contains",
        "value": "dental",
        "points": 3,
        "why": "a dental practice",
    },
    {
        "column": "Company",
        "op": "contains",
        "value": "Dental",
        "points": 2,
        "why": "a dental practice",
    },
    {
        "column": "Notes",
        "op": "contains",
        "value": "clinic",
        "points": 2,
        "why": "runs clinics",
    },
    {
        "column": "City",
        "op": "equals",
        "value": "Pune",
        "points": 1,
        "why": "in Pune, where we work",
    },
]


@pytest.mark.asyncio
async def test_an_excel_list_is_ranked_and_only_the_fits_get_cards(org):
    data = _xlsx(LIST)
    uuid = await _attach(org, data, "leads-october.xlsx")
    picked = [
        (
            "Asha Rao",
            "asha.rao@lotusdental.example.com",
            "Lotus Dental Care",
            "Owner of two dental clinics in Pune",
        ),
        (
            "Neha Iyer",
            "neha@brightteeth.example.net",
            "Bright Teeth Kids",
            "Runs a paediatric dental chain in Pune",
        ),
        (
            "Vikram Shah",
            "vikram@smilestudio.example.org",
            "Smile Studio",
            "Manages an evening dental clinic",
        ),
    ]
    replies = [
        _call(
            1, "rank_table", {"file": "leads-october.xlsx", "rules": RULES, "limit": 10}
        ),
        _call(
            2,
            "save_prospects",
            {
                "prospects": [
                    {
                        "name": n,
                        "email": e,
                        "company": c,
                        "note": w,
                        "source_url": "attached list",
                    }
                    for n, e, c, w in picked
                ]
            },
        ),
        _call(
            3,
            "draft_outreach",
            {
                "emails": [
                    {
                        "to": e,
                        "name": n,
                        "company": c,
                        "subject": f"Calls at {c}",
                        "body": f"Hi {n.split()[0]},\n\nA short note about the calls {c} misses.\n\nRavi",
                        "why": w,
                    }
                    for n, e, c, w in picked
                ]
            },
        ),
        ModelReply(
            text="Three fit and are on cards; Mehta & Co is an accounting firm, so I left it out."
        ),
    ]
    model, patches = _turn(
        replies,
        extra=(patch.object(tables, "_download", new=AsyncMock(return_value=data)),),
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        said = await decibyl.answer(
            org,
            "Here is my lead list. Which of these should I write to, and why?",
            attachments=[{"document_uuid": uuid, "filename": "leads-october.xlsx"}],
        )
    # The Excel list reached the model as text, every row with its header.
    seen = _seen_by_model(model)
    assert (
        "Name: Rahul Mehta" in seen
        and "Email: asha.rao@lotusdental.example.com" in seen
    )
    # The ranking read every row of the workbook and kept its reasons.
    ranked = _results(model)[0]
    assert ranked.get("status") == "success", ranked
    top = json.dumps(ranked)
    assert "Lotus Dental Care" in top and "a dental practice" in top
    # One card per picked lead, each saying why; none for the accountant.
    cards = await db_client.agent_events(
        organization_id=org,
        kinds=[AgentEventKind.ACTION_PROPOSED.value],
        assistant_thread=True,
    )
    recipients = sorted(
        c.payload["args"]["arguments"]["recipient_email"] for c in cards
    )
    assert recipients == sorted(e for _, e, _, _ in picked)
    assert all("Outreach to" in c.payload["why"] for c in cards)
    assert "accounting" in said.lower()


@pytest.mark.asyncio
async def test_a_pasted_link_is_read_then_the_search_is_priced_first(org):
    async with db_client.async_session() as session:
        await organization_credentials.set_credential(
            session,
            organization_id=org,
            actor_user_id=None,
            component=CostComponent.DATA,
            provider="treg",
            api_key="treg-token",
        )
        await session.commit()
    page = {
        "status": "success",
        "url": "https://smileline.example.com",
        "text": "SmileLine: an AI receptionist for dental clinics in Pune. Answers every call in Hindi, Marathi and English.",
    }
    fetch = AsyncMock(return_value=page)
    replies = [
        _call(1, "web_fetch", {"url": "https://smileline.example.com"}),
        _call(
            2,
            "find_leads",
            {
                "titles": ["Clinic owner"],
                "locations": ["Pune"],
                "industries": ["dental clinics"],
                "limit": 10,
            },
        ),
        ModelReply(
            text="Your customers are dental clinic owners in Pune. The search costs about $0.08; shall I run it?"
        ),
    ]
    model, patches = _turn(
        replies, extra=(patch("api.services.workflow.web_tools.fetch", fetch),)
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await decibyl.answer(org, "https://smileline.example.com -- find me customers")
    assert fetch.await_args.args[1]["url"] == "https://smileline.example.com"
    priced = _results(model)[1]
    assert priced["status"] == "estimate"
    assert priced["provider"] == "Treg"
    assert priced["estimate"]["at_most"] > 0
    estimates = await db_client.agent_events(
        organization_id=org,
        kinds=[AgentEventKind.ACTIVITY.value],
        assistant_thread=True,
    )
    assert any("Nothing spent yet" in (e.summary or "") for e in estimates)
