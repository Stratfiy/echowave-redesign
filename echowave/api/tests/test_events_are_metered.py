"""Meter what is unmetered today (KAN-56).

Every event a bot performs is charged in credits or marked included; a
retried job charges once; the builder's allowance is a plan cap with a
price past it. The acceptance criterion, as the ticket puts it: every event
kind in the timeline carries a credit cost or an explicit "included" marker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from api.db.models import CreditLedgerModel, OrganizationModel
from api.enums import AgentEventKind, CreditLedgerKind
from api.services.agent_builder import limits
from api.services.billing import events


class TestThePrices:
    def test_the_ticket_figures(self):
        assert events.EVENT_CREDITS == {
            "text_reply": 1,
            "knowledge_answer": 2,
            "routine_run": 2,
            # KAN-137, study §25: one event, one turn.
            "trigger_run": 1,
            # KAN-140 P1: a task handed to a bot, one turn.
            "task_run": 1,
            # Step 20, decided 15 Sept 2026: one script in the sandbox, the
            # app calls inside it not counted, Everyday and above.
            "script_run": 4,
            "tool_call": 1,
            "tool_call_premium": 3,
            "builder_message": 5,
            "number_verification": 2,
            "translation": 1,
        }
        assert events.FREE_NUMBER_VERIFICATIONS == 2
        assert limits.PAST_ALLOWANCE_CREDITS == 5

    def test_paise_are_fifty_a_credit(self):
        assert events.paise_for(events.KNOWLEDGE_ANSWER) == 100
        assert events.paise_for(events.TOOL_CALL, quantity=3) == 150

    def test_every_timeline_kind_is_priced_or_included(self):
        """The acceptance criterion. A kind added next year without a row
        here is a kind nobody decided about."""
        missing = {k.value for k in AgentEventKind} - set(events.TIMELINE_PRICES)
        assert not missing, missing
        for kind, price in events.TIMELINE_PRICES.items():
            assert price == events.INCLUDED or price in events.EVENT_CREDITS, kind

    def test_a_call_is_included_because_its_minutes_are_charged(self):
        for kind in ("call_started", "call_answered", "call_ended", "credits_settled"):
            assert events.timeline_price(kind) == {"credits": 0, "included": True}

    def test_a_message_is_a_text_reply_unless_the_row_says_otherwise(self):
        assert events.timeline_price("message") == {"credits": 1, "included": False}
        assert events.timeline_price("message", {"credits": 2}) == {
            "credits": 2,
            "included": False,
        }

    def test_a_deliverable_is_a_routine_run(self):
        assert events.timeline_price("deliverable")["credits"] == 2

    def test_an_unknown_kind_renders_as_included_rather_than_failing(self):
        assert events.timeline_price("something_invented_next_year")["included"]

    def test_a_turn_that_read_the_documents_is_a_knowledge_answer(self):
        turn = {
            "events": [
                {
                    "type": "tool_call_started",
                    "payload": {"function_name": "retrieve_from_knowledge_base"},
                }
            ]
        }
        assert events.event_for_turn(turn) == events.KNOWLEDGE_ANSWER
        assert events.event_for_turn({"events": []}) == events.TEXT_REPLY
        assert events.event_for_turn(None) == events.TEXT_REPLY

    def test_the_last_turn_is_read_defensively(self):
        assert events.last_turn_of(SimpleNamespace(revision=1)) is None
        assert events.last_turn_of(SimpleNamespace(session_data={"turns": []})) is None
        turn = {"id": "turn_1", "events": []}
        assert (
            events.last_turn_of(SimpleNamespace(session_data={"turns": [turn]})) is turn
        )

    def test_a_premium_connector_is_three(self, monkeypatch):
        monkeypatch.setattr(events, "PREMIUM_CONNECTORS", frozenset({"salesforce"}))
        assert events.tool_call_event("Salesforce") == events.TOOL_CALL_PREMIUM
        assert events.tool_call_event("gmail") == events.TOOL_CALL
        assert events.tool_call_event(None) == events.TOOL_CALL

    def test_systems_of_record_are_premium_by_default(self):
        """Decided 14 Sept (KAN-47): CRMs, ERP and accounting, commerce and
        logistics, payments, helpdesks. The env var replaces the list."""
        assert events.PREMIUM_CONNECTORS == events.DEFAULT_PREMIUM_CONNECTORS
        assert {"salesforce", "tally", "shopify", "razorpay", "zendesk"} <= (
            events.PREMIUM_CONNECTORS
        )
        assert "gmail" not in events.PREMIUM_CONNECTORS


class TestTheMonth:
    def test_the_month_rolls_over_at_midnight_ist(self):
        # 18:30 UTC on 30 Sept is 00:00 IST on 1 Oct.
        before = datetime(2026, 9, 30, 18, 29, tzinfo=UTC)
        after = datetime(2026, 9, 30, 18, 30, tzinfo=UTC)
        assert limits.ist_month(before) == "2026-09"
        assert limits.ist_month(after) == "2026-10"

    def test_the_reset_is_the_first_of_next_month_ist(self):
        moment = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
        assert limits.next_ist_month_start(moment) == datetime(
            2026, 9, 30, 18, 30, tzinfo=UTC
        )
        december = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)
        assert limits.next_ist_month_start(december) == datetime(
            2026, 12, 31, 18, 30, tzinfo=UTC
        )

    def test_the_state_reads_the_allowance(self):
        now = datetime.now(UTC)
        inside = limits.LimitState(used=30, limit=30, resets_at=now)
        assert not inside.past_allowance and inside.remaining == 0
        past = limits.LimitState(used=31, limit=30, resets_at=now)
        assert past.past_allowance and past.exhausted
        unlimited = limits.LimitState(used=5_000, limit=None, resets_at=now)
        assert not unlimited.past_allowance and unlimited.remaining is None


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _rows(session, org):
    return (
        await session.scalars(
            select(CreditLedgerModel)
            .where(CreditLedgerModel.organization_id == org.id)
            .order_by(CreditLedgerModel.id)
        )
    ).all()


@pytest.mark.asyncio
class TestVerifyingANumber:
    """The first two numbers are free; a third is two credits, once."""

    class _Db:
        def __init__(self, held: int):
            self.held = held
            self.rows: dict[str, object] = {}

        async def get_verified_number(self, organization_id, number):
            return self.rows.get(number)

        async def list_verified_numbers(self, organization_id):
            return [object()] * self.held + list(self.rows.values())

        async def upsert_verified_number_challenge(self, organization_id, number, **kw):
            self.rows[number] = SimpleNamespace(
                send_count=1, last_sent_at=kw.get("sent_at"), **kw
            )

    async def _start(self, held: int, number="+919876543210"):
        from api.services.telephony import verified_numbers as vn

        charged: list[dict] = []

        async def _charge(**kwargs):
            charged.append(kwargs)
            return 100

        db = self._Db(held)
        await vn.start_verification(7, number, db=db, charge=_charge)
        return charged, db

    async def test_the_first_two_numbers_are_free(self):
        charged, _ = await self._start(held=1)
        assert charged == []

    async def test_the_third_is_two_credits_keyed_on_the_number(self):
        charged, _ = await self._start(held=2)
        assert len(charged) == 1
        assert charged[0]["event"] == events.NUMBER_VERIFICATION
        assert charged[0]["ref_id"] == "7:919876543210"  # normalised, no plus

    async def test_a_resend_is_not_a_new_number(self):
        from datetime import timedelta

        from api.services.telephony import verified_numbers as vn

        charged: list[dict] = []

        async def _charge(**kwargs):
            charged.append(kwargs)
            return 100

        db = self._Db(2)
        first = datetime.now(UTC) - timedelta(hours=1)
        await vn.start_verification(
            7, "+919876543210", db=db, charge=_charge, now=first
        )
        await vn.start_verification(7, "+919876543210", db=db, charge=_charge)
        assert len(charged) == 1


@pytest.mark.asyncio
class TestACharge:
    async def test_it_is_one_usage_row_at_the_price(self, db_session, async_session):
        org = await _org(async_session, "charge")
        paise = await events.charge(
            async_session,
            organization_id=org.id,
            event=events.KNOWLEDGE_ANSWER,
            ref_id="336:turn_abc",
            note="Sales bot in a channel",
        )
        assert paise == 100
        rows = await _rows(async_session, org)
        assert len(rows) == 1
        assert rows[0].kind == CreditLedgerKind.USAGE.value
        assert rows[0].delta_paise == -100
        assert rows[0].ref_type == "knowledge_answer"
        assert rows[0].ref_id == "336:turn_abc"
        assert rows[0].note.startswith("Knowledge answer · 2 credits")

    async def test_the_same_ref_charges_once(self, db_session, async_session):
        org = await _org(async_session, "twice")
        first = await events.charge(
            async_session, organization_id=org.id, event=events.ROUTINE_RUN, ref_id="91"
        )
        second = await events.charge(
            async_session, organization_id=org.id, event=events.ROUTINE_RUN, ref_id="91"
        )
        assert (first, second) == (100, 0)
        assert len(await _rows(async_session, org)) == 1

    async def test_a_different_event_on_the_same_ref_is_its_own_row(
        self, db_session, async_session
    ):
        org = await _org(async_session, "tworefs")
        await events.charge(
            async_session,
            organization_id=org.id,
            event=events.TOOL_CALL,
            ref_id="336:c1",
        )
        await events.charge(
            async_session,
            organization_id=org.id,
            event=events.TEXT_REPLY,
            ref_id="336:c1",
        )
        assert len(await _rows(async_session, org)) == 2

    async def test_an_internal_account_is_never_charged(
        self, db_session, async_session
    ):
        org = await _org(async_session, "internal")
        with patch(
            "api.services.billing.internal_accounts.is_internal",
            AsyncMock(return_value=True),
        ):
            paise = await events.charge(
                async_session,
                organization_id=org.id,
                event=events.TEXT_REPLY,
                ref_id="1",
            )
        assert paise == 0
        assert await _rows(async_session, org) == []

    async def test_an_unknown_event_is_refused_not_priced_at_zero(
        self, db_session, async_session
    ):
        org = await _org(async_session, "unknown")
        with pytest.raises(KeyError):
            await events.charge(
                async_session, organization_id=org.id, event="verification", ref_id="1"
            )


@pytest.mark.asyncio
class TestTheBuilderAllowance:
    """Past the plan's monthly allowance a message is five credits, taken
    before the model runs; refused with the way out named when the balance
    cannot cover it."""

    def _client(self, user):
        from contextlib import asynccontextmanager

        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import get_user

        @asynccontextmanager
        async def _ctx():
            async def _override():
                return user

            app.dependency_overrides[get_user] = _override
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    yield client
            finally:
                app.dependency_overrides.pop(get_user, None)

        return _ctx()

    async def _user(self, session, org):
        from api.db.models import UserModel

        user = UserModel(provider_id=f"user-{org.provider_id}")
        session.add(user)
        await session.flush()
        user.selected_organization_id = org.id
        return user

    def _patches(self, *, used: int, allowance: int | None):
        now = datetime.now(UTC)
        state = limits.LimitState(
            used=used, limit=allowance, resets_at=limits.next_ist_month_start(now)
        )
        turn = SimpleNamespace(
            reply="Tell me about your clinic",
            conversation=[],
            actions=[],
            created_workflow_id=None,
        )
        return (
            patch(
                "api.routes.agent_builder.limits.check_and_consume",
                AsyncMock(return_value=state),
            ),
            patch(
                "api.routes.agent_builder._allowance", AsyncMock(return_value=allowance)
            ),
            patch(
                "api.routes.agent_builder.settings.resolve_model",
                AsyncMock(return_value=SimpleNamespace(provider="anthropic")),
            ),
            patch("api.routes.agent_builder.run_turn", AsyncMock(return_value=turn)),
        )

    async def test_inside_the_allowance_nothing_is_charged(
        self, db_session, async_session
    ):
        org = await _org(async_session, "builder-in")
        user = await self._user(async_session, org)
        await async_session.commit()
        p1, p2, p3, p4 = self._patches(used=30, allowance=30)
        with p1, p2, p3, p4:
            async with self._client(user) as client:
                response = await client.post(
                    "/api/v1/agent-builder/chat", json={"message": "hi", "history": []}
                )
        assert response.status_code == 200, response.text
        assert response.json()["usage"]["charged_credits"] == 0
        assert response.json()["usage"]["past_allowance"] is False
        assert await _rows(async_session, org) == []

    async def test_past_it_a_message_is_five_credits(self, db_session, async_session):
        org = await _org(async_session, "builder-past")
        user = await self._user(async_session, org)
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=1_000,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=1_000,
            )
        )
        await async_session.commit()
        p1, p2, p3, p4 = self._patches(used=31, allowance=30)
        with p1, p2, p3, p4:
            async with self._client(user) as client:
                response = await client.post(
                    "/api/v1/agent-builder/chat", json={"message": "hi", "history": []}
                )
        assert response.status_code == 200, response.text
        assert response.json()["usage"]["charged_credits"] == 5
        rows = await _rows(async_session, org)
        assert [r.delta_paise for r in rows] == [1_000, -250]
        assert rows[1].ref_type == "builder_message"

    async def test_with_no_credit_it_is_refused_and_told_the_way_out(
        self, db_session, async_session
    ):
        org = await _org(async_session, "builder-broke")
        user = await self._user(async_session, org)
        await async_session.commit()
        p1, p2, p3, p4 = self._patches(used=31, allowance=30)
        with p1, p2, p3, p4:
            async with self._client(user) as client:
                response = await client.post(
                    "/api/v1/agent-builder/chat", json={"message": "hi", "history": []}
                )
        assert response.status_code == 402
        assert "5 credits" in response.json()["detail"]
        assert "upgrade" in response.json()["detail"]
        assert await _rows(async_session, org) == []

    async def test_unlimited_never_charges(self, db_session, async_session):
        org = await _org(async_session, "builder-scale")
        user = await self._user(async_session, org)
        await async_session.commit()
        p1, p2, p3, p4 = self._patches(used=5_000, allowance=None)
        with p1, p2, p3, p4:
            async with self._client(user) as client:
                response = await client.post(
                    "/api/v1/agent-builder/chat", json={"message": "hi", "history": []}
                )
        assert response.status_code == 200, response.text
        assert response.json()["usage"]["charged_credits"] == 0
        assert response.json()["usage"]["allowance"] is None


@pytest.mark.asyncio
class TestTheTimelineSaysWhatEachRowCost:
    async def test_rows_carry_credits_or_included(self):
        from api.routes.agent_timeline import timeline

        rows = [
            SimpleNamespace(
                id=1,
                at=datetime.now(UTC),
                kind="message",
                actor="agent",
                summary="Here is the policy",
                payload={"credits": 2, "billed_as": "knowledge_answer"},
                is_deliverable=False,
                workflow_id=7,
                workflow_run_id=None,
                folder_id=None,
            ),
            SimpleNamespace(
                id=2,
                at=datetime.now(UTC),
                kind="call_ended",
                actor="agent",
                summary="Call · 2m10s",
                payload={},
                is_deliverable=False,
                workflow_id=7,
                workflow_run_id=None,
                folder_id=None,
            ),
        ]
        with patch(
            "api.routes.agent_timeline.db_client.agent_events",
            AsyncMock(return_value=rows),
        ):
            response = await timeline(user=SimpleNamespace(selected_organization_id=42))
        by_id = {e.id: e for e in response.events}
        assert (by_id[1].credits, by_id[1].included) == (2, False)
        assert (by_id[2].credits, by_id[2].included) == (0, True)
