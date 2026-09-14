"""The pricing decisions of 14 September (KAN-47), pinned.

Packs from ₹999 with the ₹500 gated; a translation billed per 100
characters; an empty knowledge answer billed as a plain reply; the systems
of record at the premium rate; and a call priced past the plan recording
that it was.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import OrganizationModel
from api.services.billing import events, topup_packs

NOW = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)


class TestThePackLadder:
    def test_nine_ninety_nine_is_the_floor_and_five_hundred_is_gated(self):
        assert [(p.price_paise // 100, p.credits) for p in topup_packs.PACKS] == [
            (500, 1_000),
            (999, 2_000),
            (4_999, 10_500),
            (19_999, 44_000),
        ]
        assert topup_packs.pack_for("p500").restricted
        assert not any(p.restricted for p in topup_packs.PACKS[1:])

    def test_the_public_list_starts_at_nine_ninety_nine(self):
        codes = [row["code"] for row in topup_packs.packs_as_dicts()]
        assert codes == ["p999", "p4999", "p19999"]
        everyone = topup_packs.packs_as_dicts(include_restricted=True)
        assert everyone[0]["code"] == "p500"

    def test_the_bonus_rungs_are_five_and_ten_percent(self):
        # ₹999 buys 2,000 credits: ₹1 of bonus, honestly a rounding of the price.
        assert topup_packs.pack_for("p999").bonus_credits == 2
        assert topup_packs.pack_for("p4999").bonus_credits == 502
        assert topup_packs.pack_for("p19999").bonus_credits == 4_002


class TestTranslationIsPerHundredCharacters:
    def test_rounded_up_with_a_minimum_of_one(self):
        assert events.translation_quantity("") == 1
        assert events.translation_quantity("x" * 100) == 1
        assert events.translation_quantity("x" * 101) == 2
        assert events.translation_quantity("x" * 250) == 3

    def test_it_is_one_credit_a_unit(self):
        assert events.credits_for(events.TRANSLATION) == 1
        assert events.paise_for(events.TRANSLATION, 3) == 150


class TestAnEmptyKnowledgeAnswerIsAReply:
    def _turn(self, status: str | None, *, with_result: bool = True) -> dict:
        turn_events = [
            {
                "type": "tool_call_started",
                "payload": {
                    "function_name": events.KNOWLEDGE_TOOL_NAME,
                    "tool_call_id": "c1",
                },
            }
        ]
        if with_result:
            turn_events.append(
                {
                    "type": "tool_call_result",
                    "payload": {
                        "function_name": events.KNOWLEDGE_TOOL_NAME,
                        "tool_call_id": "c1",
                        "result": {"status": status, "chunks": []},
                    },
                }
            )
        return {"events": turn_events}

    def test_a_match_is_a_knowledge_answer(self):
        assert events.event_for_turn(self._turn("ok")) == events.KNOWLEDGE_ANSWER

    def test_no_match_is_a_plain_reply(self):
        assert events.event_for_turn(self._turn("no_match")) == events.TEXT_REPLY
        assert events.event_for_turn(self._turn("unavailable")) == events.TEXT_REPLY

    def test_an_older_turn_without_a_result_keeps_the_old_price(self):
        assert (
            events.event_for_turn(self._turn(None, with_result=False))
            == events.KNOWLEDGE_ANSWER
        )

    def test_no_retrieval_at_all_is_a_reply(self):
        assert events.event_for_turn({"events": []}) == events.TEXT_REPLY


class TestSystemsOfRecordArePremium:
    def test_the_default_list_is_the_decided_one(self):
        for slug in ("salesforce", "tally", "shopify", "razorpay", "zendesk"):
            assert events.tool_call_event(slug) == events.TOOL_CALL_PREMIUM
        for slug in ("gmail", "googlesheets", "slack", "notion", None):
            assert events.tool_call_event(slug) == events.TOOL_CALL
        assert events.credits_for(events.TOOL_CALL_PREMIUM) == 3


@pytest.mark.asyncio
class TestWhoMayBuyTheGatedPack:
    async def _org(self, session, slug: str, **fields) -> OrganizationModel:
        org = OrganizationModel(
            provider_id=f"org-{slug}", quota_decibyl_tokens=0, **fields
        )
        session.add(org)
        await session.flush()
        return org

    async def test_an_ordinary_account_sees_three_packs(
        self, db_session, async_session
    ):
        org = await self._org(async_session, "plain")
        rows = await topup_packs.packs_for(async_session, organization_id=org.id)
        assert [r["code"] for r in rows] == ["p999", "p4999", "p19999"]

    async def test_an_early_adopter_sees_the_five_hundred_until_the_date(
        self, db_session, async_session
    ):
        org = await self._org(
            async_session,
            "early",
            early_adopter_until=datetime.now(UTC) + timedelta(days=30),
        )
        assert await topup_packs.may_buy_restricted(
            async_session, organization_id=org.id
        )
        rows = await topup_packs.packs_for(async_session, organization_id=org.id)
        assert rows[0]["code"] == "p500"

    async def test_early_ends_on_the_date(self, db_session, async_session):
        org = await self._org(
            async_session,
            "late",
            early_adopter_until=datetime.now(UTC) - timedelta(days=1),
        )
        assert not await topup_packs.may_buy_restricted(
            async_session, organization_id=org.id
        )

    async def test_the_order_refuses_it_to_everyone_else(
        self, db_session, async_session
    ):
        from api.services.billing import payments

        org = await self._org(async_session, "refused")
        with pytest.raises(payments.PaymentError, match="start at ₹999"):
            await payments.create_topup_order(
                async_session, organization_id=org.id, pack_code="p500", created_by=None
            )
