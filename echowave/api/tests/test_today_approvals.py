"""Pending approvals and the exact approval screen (screen 08; the approval
dock above the composer; design "Approval state").

Done when: only genuinely pending cards are in the queue and the badge is the
server's count; the preview shows the exact act in full -- recipient, amount,
content, attachments -- with account digits masked; a card on someone's
private conversation is theirs alone; another workspace's card is not here;
the dock works with only its own switch on; and approving through the
controls card with the version shown runs once from two places, while an
old version is refused.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from api import constants
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.today import approvals
from api.services.today.scope import NotFound
from api.services.workflow import actions
from api.tests.today_helpers import clean, client_as, make_people, switch_on


@pytest.fixture
async def people(test_engine):
    p = await make_people()
    yield p
    await clean(p)


def _send(**arguments) -> dict:
    payload = {
        "state": actions.PROPOSED,
        "label": "Pay Acme Print's invoice",
        "action": actions.RUN_TOOL,
        "effect": "Runs in razorpay and reaches people there. It cannot be undone.",
        "reaches_people": True,
        "why": "The invoice is due today.",
        "args": {
            "tool_uuid": "t-1",
            "tool_name": "RAZORPAY_PAY",
            "toolkit": "razorpay",
            "arguments": arguments
            or {
                "to": "accounts@acmeprint.example",
                "amount": "4800",
                "account": "001234567890",
                "body": "Invoice INV-2207 for October printing, " + "detail " * 60,
                "attachments": ["INV-2207.pdf", "PO-118.pdf"],
            },
        },
    }
    payload["version"] = actions.payload_version(payload)
    return payload


async def _card(
    org: int, payload: dict | None = None, *, thread_id: str | None = None
) -> int:
    payload = payload or _send()
    return int(
        await db_client.record_agent_event(
            organization_id=org,
            kind=AgentEventKind.ACTION_PROPOSED.value,
            actor=AgentEventActor.AGENT.value,
            summary=payload["label"],
            payload=payload,
            thread_id=thread_id,
        )
    )


async def _said(org: int, author: int, thread_id: str) -> None:
    await db_client.record_agent_event(
        organization_id=org,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary="Pay Acme",
        payload={"body": "Pay Acme", "author_id": author},
        thread_id=thread_id,
    )


@pytest.mark.asyncio
class TestTheQueue:
    async def test_only_pending_cards_and_the_count(self, people):
        org = people.me.organization_id
        waiting = await _card(org)
        done = _send(to="x@example.com")
        done["state"] = actions.DONE
        await _card(org, done)
        queue = await approvals.pending(people.me)
        assert queue["count"] == 1
        assert [i["id"] for i in queue["items"]] == [waiting]
        assert (
            queue["items"][0]["sentence"]
            == "Decibyl wants to: Pay Acme Print's invoice: ₹4,800"
        )

    async def test_another_workspace_sees_none_of_it(self, people):
        await _card(people.me.organization_id)
        assert (await approvals.pending(people.stranger))["count"] == 0


@pytest.mark.asyncio
class TestTheExactPreview:
    async def test_everything_is_shown_in_full(self, people):
        event_id = await _card(people.me.organization_id)
        view = await approvals.preview(people.me, event_id)
        assert view["recipient"] == "accounts@acmeprint.example"
        assert view["amount"] == "₹4,800"
        assert view["account"] == "•••• 7890"
        assert view["attachments"] == ["INV-2207.pdf", "PO-118.pdf"]
        # Never truncated.
        assert view["content"].count("detail") == 60
        assert (
            view["detail"]
            == "To accounts@acmeprint.example · ₹4,800 · From •••• 7890 · In razorpay"
        )
        assert view["consequence"].endswith("It cannot be undone.")
        assert view["screen_state"] == "pending"
        assert view["version"] == actions.payload_version(_send())
        assert view["expires_at"] is None

    async def test_a_card_from_another_workspace_is_not_here(self, people):
        event_id = await _card(people.me.organization_id)
        with pytest.raises(NotFound):
            await approvals.preview(people.stranger, event_id)

    async def test_states_read_as_the_screen_names_them(self):
        for state, screen in (
            (actions.ARMED, "approved"),
            (actions.RUNNING, "executing"),
            (actions.OUTCOME_UNKNOWN, "outcome_unknown"),
            (actions.DECLINED, "cancelled"),
        ):
            payload = _send()
            payload["state"] = state
            assert approvals.describe(payload)["screen_state"] == screen


@pytest.mark.asyncio
class TestPrivateConversations:
    async def test_a_card_on_my_private_thread_is_mine(self, people, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        org = people.me.organization_id
        thread = str(uuid4())
        await _said(org, people.me.user_id, thread)
        event_id = await _card(org, thread_id=thread)
        assert (await approvals.pending(people.me))["count"] == 1
        assert (await approvals.pending(people.colleague))["count"] == 0
        with pytest.raises(NotFound):
            await approvals.preview(people.colleague, event_id)

    async def test_with_private_threads_off_the_workspace_shares_it(self, people):
        org = people.me.organization_id
        thread = str(uuid4())
        await _said(org, people.me.user_id, thread)
        await _card(org, thread_id=thread)
        assert (await approvals.pending(people.colleague))["count"] == 1


@pytest.mark.asyncio
class TestApprovingIsTheControlsCard:
    async def test_two_places_approving_one_version_arm_it_once(
        self, people, monkeypatch
    ):
        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
        org = people.me.organization_id
        event_id = await _card(org)
        version = (await approvals.preview(people.me, event_id))["version"]
        with (
            patch.object(actions.approvals, "check", new=AsyncMock()),
            patch.object(actions, "_audit", new=AsyncMock()),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as queued,
        ):
            results = await asyncio.gather(
                actions.settle(
                    organization_id=org,
                    event_id=event_id,
                    verb="confirm",
                    user_id=people.me.user_id,
                    version=version,
                ),
                actions.settle(
                    organization_id=org,
                    event_id=event_id,
                    verb="confirm",
                    user_id=people.me.user_id,
                    version=version,
                ),
                return_exceptions=True,
            )
        assert sum(1 for r in results if isinstance(r, dict)) == 1
        assert queued.await_count == 1
        assert (await approvals.pending(people.me))["count"] == 0

    async def test_an_old_version_is_refused(self, people, monkeypatch):
        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
        org = people.me.organization_id
        event_id = await _card(org)
        with patch.object(actions.audit_log, "record", new=AsyncMock()):
            await actions.revise(
                organization_id=org,
                event_id=event_id,
                arguments={"to": "someone-else@example.com", "amount": "4800"},
                user_id=people.me.user_id,
            )
        view = await approvals.preview(people.me, event_id)
        assert view["recipient"] == "someone-else@example.com" and view["revisions"]
        with pytest.raises(actions.ActionError):
            await actions.settle(
                organization_id=org,
                event_id=event_id,
                verb="confirm",
                user_id=people.me.user_id,
                version=_send()["version"],
            )


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_routes_are_not_there(self, people):
        async with client_as(people.me_user) as client:
            assert (await client.get("/api/v1/today/approvals")).status_code == 404

    async def test_the_dock_switch_alone_opens_the_approvals(self, people, monkeypatch):
        switch_on(monkeypatch, "approval_dock")
        event_id = await _card(people.me.organization_id)
        async with client_as(people.me_user) as client:
            queue = (await client.get("/api/v1/today/approvals")).json()
            assert queue["count"] == 1 and queue["items"][0]["id"] == event_id
            one = await client.get(f"/api/v1/today/approvals/{event_id}")
            assert (
                one.status_code == 200
                and one.json()["recipient"] == "accounts@acmeprint.example"
            )
            # The rest of Today stays off.
            assert (await client.get("/api/v1/today")).status_code == 404
        async with client_as(people.stranger_user) as client:
            assert (
                await client.get(f"/api/v1/today/approvals/{event_id}")
            ).status_code == 404
