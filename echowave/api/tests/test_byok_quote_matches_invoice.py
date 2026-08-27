"""The BYOK quote and the BYOK invoice must be the same number.

A customer bringing their own voice key pays no provider line for it — they
already paid that vendor — and pays an uplifted **platform rate** instead. Both
halves of that trade have to appear on both surfaces. The estimator zeroed the
provider line and left the platform line alone, so the wizard quoted a minute
*cheaper* than the invoice charged for it, by the whole uplift: on the default
tiers the quoted fee was ₹1.92 against ₹3.36 billed, 75% low on our own
revenue line and discovered by the customer on their first invoice.

These tests do not assert the uplift against a constant written here — a
constant written in a test is a second copy of the decision, and it would pass
happily while the product charged something else. They assert that the rate the
quote shows and the rate the receipt applies are the *same integer*, arrived at
through the same call.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api import constants
from api.db.models import (
    OrganizationModel,
    ProviderRateModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.billing.costing import cost_workflow_run
from api.services.billing.estimator import estimate_cost_per_minute
from api.services.billing.usage import byok_tier, byok_uplift_micros_usd
from api.services.configuration import managed_tiers


@pytest.fixture(autouse=True)
def tiered_fee_on(monkeypatch):
    """The flag these tests are about. Off in production until Phase 2, which
    is exactly why the divergence went unnoticed — with it off both surfaces
    agree trivially and prove nothing."""
    monkeypatch.setattr(constants, "BYOK_TIERED_FEE_ENABLED", True)


def _rate(provider, component, unit, mpaise, model=""):
    return ProviderRateModel(
        provider=provider,
        model=model,
        component=component,
        unit=unit,
        rate_mpaise=mpaise,
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
async def stack(async_session):
    """The managed default tiers, priced, so neither surface falls to zero."""
    stt = managed_tiers.resolve("stt", "default")
    llm = managed_tiers.resolve("llm", "default")
    tts = managed_tiers.resolve("tts", "default")
    async_session.add_all(
        [
            _rate(stt.provider, "stt", "minute", 60_000, model=stt.model),
            _rate(llm.provider, "llm", "1k_tokens", 15_000, model=llm.model),
            _rate(tts.provider, "tts", "1k_chars", 180_000, model=tts.model),
        ]
    )
    await async_session.flush()
    return stt, llm, tts


async def _org(async_session, provider_id):
    org = OrganizationModel(provider_id=provider_id, quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


async def _one_minute_receipt(async_session, org, *, key_sources, stack):
    """Cost exactly one connected minute of that stack, as billed."""
    stt, llm, tts = stack
    workflow = WorkflowModel(organization_id=org.id, name="byok-quote")
    async_session.add(workflow)
    await async_session.flush()

    usage_info = {
        "call_duration_seconds": 60,
        "key_sources": key_sources,
        "llm": {
            f"{llm.provider.title()}LLMService#0|||{llm.model}": {
                "prompt_tokens": 1_400,
                "completion_tokens": 0,
            }
        },
        "stt": {f"{stt.provider.title()}STTService#0|||{stt.model}": 60},
        "tts": {f"{tts.provider.title()}TTSService#0|||{tts.model}": 2_300},
    }
    run = WorkflowRunModel(
        workflow_id=workflow.id,
        name="byok-quote-run",
        mode="twilio",
        billable_seconds=60,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        ended_at=datetime(2026, 6, 1, tzinfo=UTC),
        usage_info=usage_info,
    )
    async_session.add(run)
    await async_session.flush()
    return await cost_workflow_run(async_session, run.id)


async def _quote(async_session, org, *, customer_keyed, stack):
    stt, llm, tts = stack
    return await estimate_cost_per_minute(
        async_session,
        organization_id=org.id,
        stt_provider=stt.provider,
        stt_model=stt.model,
        llm_provider=llm.provider,
        llm_model=llm.model,
        tts_provider=tts.provider,
        tts_model=tts.model,
        customer_keyed=customer_keyed,
    )


def _platform_line(estimate):
    return next(line for line in estimate.lines if line.component == "platform")


class TestTheQuotedFeeIsTheBilledFee:
    @pytest.mark.parametrize(
        ("keyed", "key_sources"),
        [
            (
                {"tts"},
                {"llm": "managed", "stt": "managed", "tts": "byok"},
            ),
            (
                {"stt"},
                {"llm": "managed", "stt": "byok", "tts": "managed"},
            ),
            (
                {"stt", "tts"},
                {"llm": "managed", "stt": "byok", "tts": "byok"},
            ),
            (
                set(),
                {"llm": "managed", "stt": "managed", "tts": "managed"},
            ),
        ],
        ids=["own voice", "own transcription", "own both", "all managed"],
    )
    async def test_the_platform_rate_matches_to_the_millipaise(
        self, db_session, async_session, stack, keyed, key_sources
    ):
        org = await _org(async_session, f"org-byok-{'-'.join(sorted(keyed)) or 'none'}")
        quote = await _quote(async_session, org, customer_keyed=keyed, stack=stack)
        receipt = await _one_minute_receipt(
            async_session, org, key_sources=key_sources, stack=stack
        )

        assert _platform_line(quote).unit_rate_mpaise == receipt.platform_rate_mpaise

    async def test_the_quoted_fee_for_a_minute_is_what_a_minute_is_billed(
        self, db_session, async_session, stack
    ):
        """One connected minute, both ways round, in paise."""
        org = await _org(async_session, "org-byok-minute")
        quote = await _quote(async_session, org, customer_keyed={"tts"}, stack=stack)
        receipt = await _one_minute_receipt(
            async_session,
            org,
            key_sources={"llm": "managed", "stt": "managed", "tts": "byok"},
            stack=stack,
        )

        assert _platform_line(quote).paise_per_minute == receipt.platform_fee_paise


class TestBringingAKeyNeverMakesTheQuoteCheaperThanTheBill:
    async def test_own_voice_quotes_above_the_managed_platform_fee(
        self, db_session, async_session, stack
    ):
        """The direction of the defect, stated on its own.

        Zeroing the voice line without uplifting the fee is not merely a
        different number — it is a quote that is *lower* than the managed one
        for a customer who will be billed more.
        """
        org = await _org(async_session, "org-byok-direction")
        managed = await _quote(async_session, org, customer_keyed=set(), stack=stack)
        own_voice = await _quote(
            async_session, org, customer_keyed={"tts"}, stack=stack
        )

        assert (
            _platform_line(own_voice).paise_per_minute
            > _platform_line(managed).paise_per_minute
        )

    async def test_the_flag_still_gates_both_surfaces_together(
        self, db_session, async_session, stack, monkeypatch
    ):
        """With the tiered fee off, a BYOK quote is the ordinary fee again —
        and so is the invoice. The switch has to move both or it reintroduces
        the divergence it was meant to control."""
        monkeypatch.setattr(constants, "BYOK_TIERED_FEE_ENABLED", False)

        org = await _org(async_session, "org-byok-flag-off")
        quote = await _quote(async_session, org, customer_keyed={"tts"}, stack=stack)
        receipt = await _one_minute_receipt(
            async_session,
            org,
            key_sources={"llm": "managed", "stt": "managed", "tts": "byok"},
            stack=stack,
        )

        assert _platform_line(quote).unit_rate_mpaise == receipt.platform_rate_mpaise


class TestTheTierIsDecidedInOnePlace:
    """The estimator and the receipt read the same two functions, so a change
    to what a tier is worth cannot land on one surface only."""

    @pytest.mark.parametrize(
        ("brought", "expected"),
        [
            ({"tts"}, "tts"),
            ({"stt", "tts"}, "tts"),
            ({"stt"}, "stt"),
            ({"llm"}, "managed"),
            ({"llm", "stt"}, "stt"),
            (set(), "managed"),
        ],
    )
    def test_the_tier_is_cut_on_which_component_not_how_many(self, brought, expected):
        assert byok_tier(brought) == expected

    def test_a_language_model_key_costs_nothing_extra(self):
        assert byok_uplift_micros_usd(byok_tier({"llm"})) == 0

    def test_the_voice_uplift_is_the_larger_of_the_two(self):
        """Not pinned to a figure — the ordering is the decision, and a
        configuration that inverted it would price the cheap key higher."""
        assert byok_uplift_micros_usd("tts") > byok_uplift_micros_usd("stt") > 0
