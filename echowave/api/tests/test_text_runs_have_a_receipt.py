"""A text session gets a vendor-cost receipt, charged nothing.

Non-voice work is charged per event (billing/events.py) and its tokens were
recorded on the run but never written up, so margin on every chat, routine
and trigger read as 100%. Now each completed turn re-costs the run: every
model line at what it cost us, nothing charged, no ledger debit -- the event
fee is the charge. The costing sweep still leaves text runs alone.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from api import constants
from api.db.models import (
    CallCostItemModel,
    CreditLedgerModel,
    OrganizationModel,
    ProviderRateModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CostComponent, RateUnit
from api.services.billing.costing import cost_workflow_run

pytestmark = pytest.mark.asyncio


async def _text_run(session, slug: str, usage_info: dict):
    user = UserModel(provider_id=f"user-{slug}")
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add_all([user, org])
    await session.flush()
    wf = WorkflowModel(name=slug, user_id=user.id, organization_id=org.id)
    session.add(wf)
    await session.flush()
    run = WorkflowRunModel(
        name="chat",
        workflow_id=wf.id,
        mode="textchat",
        usage_info=usage_info,
        cost_info={},
        is_completed=True,
    )
    session.add(run)
    await session.flush()
    return org, run


def _rate(component, rate_mpaise, provider="openai", model=""):
    return ProviderRateModel(
        provider=provider,
        model=model,
        component=component.value,
        unit=RateUnit.THOUSAND_TOKENS.value,
        rate_mpaise=rate_mpaise,
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
    )


TURN = {
    "llm": {
        "OpenAILLMService#0|||gpt-4o-mini": {
            "prompt_tokens": 10_000,
            "completion_tokens": 900,
            "cache_read_input_tokens": 6_000,
        }
    }
}


class TestTheReceipt:
    async def test_vendor_lines_at_zero_charge_and_no_debit(
        self, async_session, monkeypatch
    ):
        monkeypatch.setattr(constants, "METERING_SPLIT_2026_09_ENABLED", False)
        org, run = await _text_run(async_session, "blended", TURN)
        async_session.add(_rate(CostComponent.LLM, 12_000))
        await async_session.flush()

        cost = await cost_workflow_run(async_session, run.id)

        assert cost is not None
        assert cost.total_charged_paise == 0
        # 10,900 tokens at 12,000 mpaise per 1k = 130.8 paise -> 131
        assert cost.total_provider_cost_paise == 131
        lines = (
            await async_session.scalars(
                select(CallCostItemModel).where(
                    CallCostItemModel.workflow_run_id == run.id
                )
            )
        ).all()
        assert [line.component for line in lines] == ["llm"]
        assert lines[0].cost_paise == 0
        assert lines[0].provider_cost_paise == 131
        debits = (
            await async_session.scalars(
                select(CreditLedgerModel).where(
                    CreditLedgerModel.organization_id == org.id
                )
            )
        ).all()
        assert debits == []
        assert run.total_charged_paise == 0
        assert run.total_provider_cost_paise == 131
        assert run.costed_at is not None

    async def test_split_on_it_is_three_lines(self, async_session, monkeypatch):
        monkeypatch.setattr(constants, "METERING_SPLIT_2026_09_ENABLED", True)
        _org, run = await _text_run(async_session, "split", TURN)
        async_session.add_all(
            [
                _rate(CostComponent.LLM_INPUT, 14_400),
                _rate(CostComponent.LLM_CACHED, 7_200),
                _rate(CostComponent.LLM_OUTPUT, 57_600),
            ]
        )
        await async_session.flush()
        cost = await cost_workflow_run(async_session, run.id)
        by = {line.component: line for line in cost.line_items}
        assert set(by) == {"llm_input", "llm_cached", "llm_output"}
        assert all(line.cost_paise == 0 for line in by.values())
        assert cost.total_provider_cost_paise == sum(
            line.provider_cost_paise for line in by.values()
        )

    async def test_the_next_turn_replaces_the_receipt(self, async_session, monkeypatch):
        monkeypatch.setattr(constants, "METERING_SPLIT_2026_09_ENABLED", False)
        _org, run = await _text_run(async_session, "again", TURN)
        async_session.add(_rate(CostComponent.LLM, 12_000))
        await async_session.flush()
        first = await cost_workflow_run(async_session, run.id, recost=True)
        run.usage_info = {
            "llm": {
                "OpenAILLMService#0|||gpt-4o-mini": {
                    "prompt_tokens": 20_000,
                    "completion_tokens": 1_800,
                }
            }
        }
        await async_session.flush()
        second = await cost_workflow_run(async_session, run.id, recost=True)
        assert second.total_provider_cost_paise == 2 * first.total_provider_cost_paise
        lines = (
            await async_session.scalars(
                select(CallCostItemModel).where(
                    CallCostItemModel.workflow_run_id == run.id
                )
            )
        ).all()
        assert len(lines) == 1

    async def test_a_call_is_still_charged(self, async_session, monkeypatch):
        """The zero-charge shape is for text runs only."""
        monkeypatch.setattr(constants, "METERING_SPLIT_2026_09_ENABLED", False)
        _org, run = await _text_run(async_session, "voice", TURN)
        run.mode = "twilio"
        run.billable_seconds = 60
        async_session.add(_rate(CostComponent.LLM, 12_000))
        await async_session.flush()
        cost = await cost_workflow_run(async_session, run.id)
        assert cost.total_charged_paise > 0
