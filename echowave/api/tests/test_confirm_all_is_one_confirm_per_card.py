"""Confirm all: one press, and still one approval per recipient.

Found building an outreach agent end to end (October 2026): ten leads, ten
send cards, and the only way to approve them was ten Confirms, one card at
a time, scrolling a thread between each. A business owner who had read all
ten drafts had no way to say "send these".

"Confirm all" must not become the shortcut that sends something nobody
read. So it is not a new kind of approval: it is the ordinary Confirm,
applied card by card, each against the version that card showed. A card
edited since, already settled, from another workspace, or named without
its version is refused on its own line and the others still go; nothing is
confirmed as a group.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import actions


async def _org() -> int:
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"confirm-all-{uuid4().hex}")
        session.add(org)
        await session.flush()
        organization_id = int(org.id)
        await session.commit()
    return organization_id


@pytest.fixture
async def two_orgs(test_engine):
    a, b = await _org(), await _org()
    yield a, b
    async with db_client.async_session() as session:
        for org in (a, b):
            for table in ("agent_task_transitions", "agent_tasks", "agent_events"):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
                )
        await session.commit()


def _send(to: str, body: str = "Hello") -> dict:
    return {
        "state": actions.PROPOSED,
        "label": "Gmail — Send Email via gmail",
        "action": actions.RUN_TOOL,
        "effect": "Runs in gmail and reaches people there. It cannot be undone.",
        "reaches_people": True,
        "args": {
            "tool_uuid": "t-1",
            "tool_name": "GMAIL_SEND_EMAIL",
            "toolkit": "gmail",
            "arguments": {"recipient_email": to, "subject": "Hi", "body": body},
        },
    }


async def _card(organization_id: int, payload: dict) -> tuple[int, str]:
    payload.setdefault("version", actions.payload_version(payload))
    event_id = int(
        await db_client.record_agent_event(
            organization_id=organization_id,
            kind=AgentEventKind.ACTION_PROPOSED.value,
            actor=AgentEventActor.AGENT.value,
            summary=payload["label"],
            payload=payload,
        )
    )
    return event_id, payload["version"]


async def _state(organization_id: int, event_id: int) -> str:
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    return dict(event.payload or {}).get("state")


def _quiet():
    return (
        patch.object(actions.approvals, "check", new=AsyncMock()),
        patch.object(actions, "_audit", new=AsyncMock()),
        patch.object(actions.audit_log, "record", new=AsyncMock()),
        patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
    )


@pytest.mark.asyncio
class TestConfirmAll:
    async def test_every_card_named_with_its_version_is_armed(self, two_orgs):
        org, _ = two_orgs
        cards = [await _card(org, _send(f"lead{i}@example.com")) for i in range(3)]
        a, b, c, d = _quiet()
        with a, b, c, d:
            results = await actions.settle_many(
                organization_id=org,
                items=[{"event_id": e, "version": v} for e, v in cards],
                user_id=1,
            )
        assert [r["ok"] for r in results] == [True, True, True]
        for event_id, _ in cards:
            assert await _state(org, event_id) == actions.ARMED

    async def test_a_card_edited_since_is_refused_and_the_rest_go(self, two_orgs):
        org, _ = two_orgs
        first = await _card(org, _send("one@example.com"))
        edited_id, shown = await _card(org, _send("two@example.com"))
        third = await _card(org, _send("three@example.com"))
        # The second card's words changed after the person read them.
        event = await db_client.get_agent_event(edited_id, organization_id=org)
        payload = dict(event.payload)
        payload["args"]["arguments"]["body"] = "Something else entirely"
        payload["version"] = actions.payload_version(payload)
        await db_client.set_agent_event_payload(
            edited_id, organization_id=org, payload=payload
        )
        a, b, c, d = _quiet()
        with a, b, c, d:
            results = await actions.settle_many(
                organization_id=org,
                items=[
                    {"event_id": first[0], "version": first[1]},
                    {"event_id": edited_id, "version": shown},
                    {"event_id": third[0], "version": third[1]},
                ],
                user_id=1,
            )
        by_id = {r["event_id"]: r for r in results}
        assert by_id[first[0]]["ok"] and by_id[third[0]]["ok"]
        assert by_id[edited_id]["ok"] is False
        assert "changed" in by_id[edited_id]["reason"].lower()
        assert await _state(org, edited_id) == actions.PROPOSED

    async def test_a_card_without_its_version_is_not_confirmed(self, two_orgs):
        """Off the ledger a single Confirm may omit the version; a bulk one
        may not, or it would approve words the screen never showed."""
        org, _ = two_orgs
        event_id, _ = await _card(org, _send("one@example.com"))
        a, b, c, d = _quiet()
        with a, b, c, d:
            results = await actions.settle_many(
                organization_id=org, items=[{"event_id": event_id}], user_id=1
            )
        assert results[0]["ok"] is False
        assert await _state(org, event_id) == actions.PROPOSED

    async def test_another_workspaces_card_is_not_touched(self, two_orgs):
        org, other = two_orgs
        theirs = await _card(other, _send("theirs@example.com"))
        a, b, c, d = _quiet()
        with a, b, c, d:
            results = await actions.settle_many(
                organization_id=org,
                items=[{"event_id": theirs[0], "version": theirs[1]}],
                user_id=1,
            )
        assert results[0]["ok"] is False
        assert await _state(other, theirs[0]) == actions.PROPOSED

    async def test_a_settled_card_is_reported_not_rearmed(self, two_orgs):
        org, _ = two_orgs
        event_id, version = await _card(org, _send("one@example.com"))
        a, b, c, d = _quiet()
        with a, b, c, d:
            await actions.settle(
                organization_id=org, event_id=event_id, verb="decline", user_id=1
            )
            results = await actions.settle_many(
                organization_id=org,
                items=[{"event_id": event_id, "version": version}],
                user_id=1,
            )
        assert results[0]["ok"] is False
        assert await _state(org, event_id) == actions.DECLINED

    async def test_the_same_card_twice_is_confirmed_once(self, two_orgs):
        org, _ = two_orgs
        event_id, version = await _card(org, _send("one@example.com"))
        a, b, c, d = _quiet()
        with a, b, c, d:
            results = await actions.settle_many(
                organization_id=org,
                items=[{"event_id": event_id, "version": version}] * 2,
                user_id=1,
            )
        assert len(results) == 1 and results[0]["ok"]

    async def test_too_many_at_once_is_refused_whole(self, two_orgs):
        org, _ = two_orgs
        with pytest.raises(actions.ActionError):
            await actions.settle_many(
                organization_id=org,
                items=[
                    {"event_id": i, "version": "x"}
                    for i in range(actions.MAX_CONFIRM_ALL + 1)
                ],
                user_id=1,
            )


@asynccontextmanager
async def _client(user):
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_user, None)


@pytest.mark.asyncio
async def test_over_http_each_card_answers_for_itself(two_orgs, monkeypatch):
    monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
    org, _ = two_orgs
    good = await _card(org, _send("one@example.com"))
    stale = await _card(org, _send("two@example.com"))
    user = SimpleNamespace(id=None, selected_organization_id=org, provider_id="p")
    a, b, c, d = _quiet()
    with a, b, c, d:
        async with _client(user) as client:
            response = await client.post(
                "/api/v1/timeline/actions/confirm-all",
                json={
                    "items": [
                        {"event_id": good[0], "version": good[1]},
                        {"event_id": stale[0], "version": "0000000000000000"},
                    ]
                },
            )
    assert response.status_code == 200, response.text
    body = response.json()
    by_id = {r["event_id"]: r for r in body["results"]}
    assert by_id[good[0]]["ok"] is True
    assert by_id[stale[0]]["ok"] is False
    assert body["confirmed"] == 1
