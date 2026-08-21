"""The quote and the invoice, reconciled to the paise, through both real paths.

Every pricing defect this codebase has shipped has had the same shape: two
pieces of code answering "what does this cost" and only one of them being
right. The estimate applied no managed markup while the receipt applied 1.4x.
The estimate documented a BYOK uplift it never applied. Add-ons were charged by
the coster and quoted by nobody. Speech-to-speech resolved to a rate under one
name and was quoted under another.

Each was found by reading. This module exists so the next one is found by CI.

It does not compare two formulas — that would only prove the formulas match. It
runs a **real call** through ``cost_workflow_run``, the same function post-call
work calls, and compares the receipt it persists against what
``estimate_cost_per_minute`` told the customer beforehand. A call is
constructed to consume in sixty seconds exactly what the estimator assumes per
minute, so the two figures are directly comparable and any difference is a real
disagreement rather than a difference in the traffic.

The flags are forced **on** throughout. Off, these charges are all zero and the
two sides agree trivially — which is exactly the state that let three of the
four defects above sit in ``main`` looking fine.
"""

import random
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from api.db.models import (
    OrganizationModel,
    ProviderRateModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CostComponent, RateUnit
from api.services.billing.addons import CALL_QA, KNOWLEDGE_BASE
from api.services.billing.costing import cost_workflow_run
from api.services.billing.estimator import (
    DEFAULT_CHARACTERS_PER_MINUTE,
    DEFAULT_TOKENS_PER_MINUTE,
    estimate_cost_per_minute,
    rate_card_provider,
)

LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)

#: The stack the reconciliation runs on. Named by the strings the *pipeline*
#: records, because that is what a receipt is priced under.
STT = "deepgram"
LLM = "openai"
TTS = "sarvam"
TELEPHONY = "plivo"


@pytest.fixture
def charges_switched_on(monkeypatch):
    """Both commercial flags on, for the duration of one test.

    ``fees.py`` reads them through ``constants`` rather than from-importing, so
    patching the module attribute is enough and no import order matters.
    """
    from api import constants

    monkeypatch.setattr(constants, "BYOK_TIERED_FEE_ENABLED", True)
    monkeypatch.setattr(constants, "ADDON_BILLING_ENABLED", True)


async def _org_with_workflow(async_session, slug: str):
    user = UserModel(provider_id=f"user-{slug}")
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add_all([user, org])
    await async_session.flush()

    workflow = WorkflowModel(
        name=f"wf-{slug}",
        user_id=user.id,
        organization_id=org.id,
        workflow_definition={},
        template_context_variables={},
        call_disposition_codes={},
    )
    async_session.add(workflow)
    await async_session.flush()
    return org, workflow


def _rate(provider, component, unit, mpaise, model=""):
    return ProviderRateModel(
        provider=provider,
        model=model,
        component=component.value,
        unit=unit.value,
        rate_mpaise=mpaise,
        effective_from=LONG_AGO,
    )


async def _seed_rates(async_session, *, realtime_provider: str | None = None):
    rates = [
        _rate(STT, CostComponent.STT, RateUnit.MINUTE, 25_000),
        _rate(LLM, CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 12_000),
        _rate(TTS, CostComponent.TTS, RateUnit.THOUSAND_CHARS, 40_000),
        _rate(TELEPHONY, CostComponent.TELEPHONY, RateUnit.MINUTE, 60_000),
    ]
    if realtime_provider:
        rates.append(
            _rate(realtime_provider, CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 9_000)
        )
    async_session.add_all(rates)
    await async_session.flush()


def _one_minute_usage(*, byok: tuple[str, ...] = (), addons: tuple[str, ...] = ()):
    """Sixty seconds consuming exactly what the estimator assumes per minute.

    That equality is the whole trick. The estimator quotes
    ``DEFAULT_TOKENS_PER_MINUTE`` tokens and ``DEFAULT_CHARACTERS_PER_MINUTE``
    characters; a call that consumes precisely those in precisely one minute
    must produce a receipt equal to the quote, or one of them is wrong.

    A BYOK component still records its usage — the pipeline measures what it
    ran regardless of whose key paid for it — and it is
    ``usage_items_from_usage_info`` that must drop it. Omitting it here would
    test the fixture rather than the code.
    """
    return {
        # ``<processor>|||<model>`` is the key shape the pipeline writes, and
        # the model half is what lets the receipt resolve a per-model rate.
        "llm": {
            "OpenAILLMService#0|||gpt-4o": {
                "prompt_tokens": 1_000,
                "completion_tokens": DEFAULT_TOKENS_PER_MINUTE - 1_000,
            }
        },
        "tts": {"SarvamTTSService#0": DEFAULT_CHARACTERS_PER_MINUTE},
        "stt": {"DeepgramSTTService#0": 60},
        "telephony": {TELEPHONY: 60},
        "key_sources": {
            "llm": "byok" if "llm" in byok else "managed",
            "stt": "byok" if "stt" in byok else "managed",
            "tts": "byok" if "tts" in byok else "managed",
            "telephony": "managed",
        },
        "addons": list(addons),
    }


async def _receipt_total(async_session, workflow, usage_info):
    """What ``cost_workflow_run`` actually charges for that call."""
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode="plivo",
        usage_info=usage_info,
        cost_info={},
        initial_context={},
        gathered_context={},
        is_completed=True,
        billable_seconds=60,
        created_at=datetime.now(UTC),
        answered_at=datetime.now(UTC),
    )
    async_session.add(run)
    await async_session.flush()

    cost = await cost_workflow_run(async_session, run.id)
    assert cost is not None
    # A component we hold no rate for would make the comparison meaningless:
    # both sides would be missing the same line and would agree on a wrong
    # number. This is the guard the product code now has too.
    assert cost.uncosted == ()
    return cost


class TestTheQuoteEqualsTheInvoice:
    """One stack, both paths, to the paise."""

    @pytest.mark.parametrize(
        ("byok", "addons"),
        [
            ((), ()),
            ((), (KNOWLEDGE_BASE,)),
            ((), (KNOWLEDGE_BASE, CALL_QA)),
            (("stt",), ()),
            (("tts",), ()),
            (("llm",), ()),
            (("stt", "tts"), (CALL_QA,)),
            (("llm", "stt", "tts"), (KNOWLEDGE_BASE, CALL_QA)),
        ],
    )
    async def test_they_agree(self, async_session, charges_switched_on, byok, addons):
        slug = f"rec-{'-'.join(byok) or 'managed'}-{len(addons)}"
        org, workflow = await _org_with_workflow(async_session, slug)
        await _seed_rates(async_session)

        estimate = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            stt_provider=STT,
            llm_provider=LLM,
            llm_model="gpt-4o",
            tts_provider=TTS,
            telephony_provider=TELEPHONY,
            customer_keyed=set(byok),
            addons=addons,
        )
        assert estimate.unpriced == ()

        cost = await _receipt_total(
            async_session, workflow, _one_minute_usage(byok=byok, addons=addons)
        )

        assert estimate.total_paise_per_minute == cost.total_charged_paise

    async def test_they_agree_across_randomised_rate_cards(
        self, async_session, charges_switched_on
    ):
        """The eight cases above use rates I chose, which is a weak guarantee.

        Both sides round once per line, and two roundings agree for most values
        and disagree for a few. Sweeping the rate card is what distinguishes
        "the formulas match" from "they matched on my example".
        """
        rng = random.Random(20260821)
        org, workflow = await _org_with_workflow(async_session, "fuzz")

        for case in range(40):
            rates = {
                "stt": rng.randrange(1, 200_000),
                "llm": rng.randrange(1, 200_000),
                "tts": rng.randrange(1, 200_000),
                "telephony": rng.randrange(1, 200_000),
            }
            async_session.add_all(
                [
                    _rate(STT, CostComponent.STT, RateUnit.MINUTE, rates["stt"]),
                    _rate(
                        LLM,
                        CostComponent.LLM,
                        RateUnit.THOUSAND_TOKENS,
                        rates["llm"],
                        model="gpt-4o",
                    ),
                    _rate(
                        TTS, CostComponent.TTS, RateUnit.THOUSAND_CHARS, rates["tts"]
                    ),
                    _rate(
                        TELEPHONY,
                        CostComponent.TELEPHONY,
                        RateUnit.MINUTE,
                        rates["telephony"],
                    ),
                ]
            )
            await async_session.flush()

            byok = tuple(c for c in ("stt", "tts", "llm") if rng.random() < 0.4)
            addons = tuple(a for a in (KNOWLEDGE_BASE, CALL_QA) if rng.random() < 0.5)

            estimate = await estimate_cost_per_minute(
                async_session,
                organization_id=org.id,
                stt_provider=STT,
                llm_provider=LLM,
                llm_model="gpt-4o",
                tts_provider=TTS,
                telephony_provider=TELEPHONY,
                customer_keyed=set(byok),
                addons=addons,
            )
            cost = await _receipt_total(
                async_session, workflow, _one_minute_usage(byok=byok, addons=addons)
            )

            assert estimate.total_paise_per_minute == cost.total_charged_paise, (
                f"case {case}: quote {estimate.total_paise_per_minute} != "
                f"invoice {cost.total_charged_paise} at rates {rates}, "
                f"byok={byok}, addons={addons}"
            )
            # And each still reconciles against its own lines, which is what
            # makes the equality above meaningful rather than two totals that
            # happen to coincide.
            assert estimate.total_paise_per_minute == sum(
                line.paise_per_minute for line in estimate.lines
            )
            assert cost.total_charged_paise == sum(
                line.cost_paise for line in cost.line_items
            )

            # Retire this case's rates so the next iteration's rows are the
            # ones in force rather than colliding with them.
            for row in await async_session.execute(select(ProviderRateModel)):
                await async_session.delete(row[0])
            await async_session.flush()

    async def test_the_uplift_is_the_whole_difference_between_two_quotes(
        self, async_session, charges_switched_on
    ):
        """Bringing the voice raises the fee by exactly the published uplift.

        $0.020 managed and $0.035 with their own voice, and nothing else on the
        quote moves except the components that stopped being ours.
        """
        from api import constants

        org, _ = await _org_with_workflow(async_session, "uplift")
        await _seed_rates(async_session)

        managed = await estimate_cost_per_minute(
            async_session, organization_id=org.id, stt_provider=STT
        )
        keyed = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            stt_provider=STT,
            customer_keyed={"tts"},
        )

        assert managed.byok_tier == "managed"
        assert managed.byok_uplift_micros_usd == 0
        assert keyed.byok_tier == "tts"
        assert keyed.byok_uplift_micros_usd == constants.BYOK_TTS_UPLIFT_MICROS_USD

        # The fee rose, and rose by the uplift converted at the same FX rate
        # the rest of the quote used — not by a separately-rounded number.
        from api.services.billing.money import platform_fee_paise, usd_to_mpaise

        uplift_mpaise = usd_to_mpaise(
            micros_usd=constants.BYOK_TTS_UPLIFT_MICROS_USD,
            usd_inr_paise=keyed.usd_inr_paise,
        )
        base_rate = managed.platform_paise_per_minute * 1_000
        assert keyed.platform_paise_per_minute == platform_fee_paise(
            billable_minutes=1, rate_mpaise=base_rate + uplift_mpaise
        )

    async def test_the_language_model_alone_never_moves_the_fee(
        self, async_session, charges_switched_on
    ):
        """The decision that stops a customer's bill going *up* for bringing a key."""
        org, _ = await _org_with_workflow(async_session, "llm-only")
        await _seed_rates(async_session)

        managed = await estimate_cost_per_minute(
            async_session, organization_id=org.id, stt_provider=STT
        )
        keyed = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            stt_provider=STT,
            customer_keyed={"llm"},
        )

        assert keyed.byok_tier == "managed"
        assert keyed.platform_paise_per_minute == managed.platform_paise_per_minute


class TestTheChargesStayInvisibleWhileSwitchedOff:
    """The state every existing account is in, and the one that hid the bugs."""

    async def test_no_uplift_and_no_addon_line_by_default(
        self, async_session, monkeypatch
    ):
        from api import constants

        monkeypatch.setattr(constants, "BYOK_TIERED_FEE_ENABLED", False)
        monkeypatch.setattr(constants, "ADDON_BILLING_ENABLED", False)

        org, _ = await _org_with_workflow(async_session, "flags-off")
        await _seed_rates(async_session)

        estimate = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            stt_provider=STT,
            customer_keyed={"tts"},
            addons=(KNOWLEDGE_BASE, CALL_QA),
        )

        assert estimate.byok_uplift_micros_usd == 0
        assert estimate.addon_paise_per_minute == 0
        assert [
            line.component for line in estimate.lines if line.component == "addon"
        ] == []


class TestSpeechToSpeechIsPricedUnderTheNameItIsBilledUnder:
    """The 9x under-quote: two names for one vendor, and nothing bridging them.

    ``managed_tiers`` names the vendor the way ``service_factory`` needs it;
    the rate card names it the way ``provider_from_processor`` derives it from
    the running pipeline. The rate lookup has to cross that gap or a realtime
    tier resolves to no rate, the model line lands in ``unpriced``, and what is
    left — telephony plus the platform fee — is served as the price.
    """

    def test_the_tier_name_maps_to_the_rate_card_name(self):
        assert rate_card_provider("openai_realtime") == "decibylopenairealtime"
        assert rate_card_provider("google_realtime") == "decibylgeminilive"
        # Already in rate-card form, and anything else, passes through.
        assert rate_card_provider("decibylgeminilive") == "decibylgeminilive"
        assert rate_card_provider("sarvam") == "sarvam"

    async def test_a_realtime_tier_resolves_to_a_rate(self, async_session):
        """The regression itself: this returned only telephony and the fee."""
        org, _ = await _org_with_workflow(async_session, "realtime")
        await _seed_rates(async_session, realtime_provider="decibylopenairealtime")

        estimate = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            # The name the tier map hands over, not the one the rate card holds.
            llm_provider="openai_realtime",
            llm_model="gpt-realtime-2",
            telephony_provider=TELEPHONY,
        )

        assert estimate.unpriced == ()
        llm = next(line for line in estimate.lines if line.component == "llm")
        assert llm.paise_per_minute > 0
        # Reported under the name the caller asked about, priced under the
        # name the receipt will use.
        assert llm.provider == "openai_realtime"

    async def test_it_uses_the_realtime_token_assumption_not_the_text_one(
        self, async_session
    ):
        """The second half of the same bug, and the quieter one.

        A realtime minute is billed on audio tokens with the whole conversation
        re-sent each turn. Falling through to the text default of 1,400 tokens
        a minute under-quotes it several-fold even once the rate is found.
        """
        from api.services.billing.estimator import REALTIME_TOKENS_PER_MINUTE

        org, _ = await _org_with_workflow(async_session, "realtime-tokens")
        await _seed_rates(async_session, realtime_provider="decibylopenairealtime")

        estimate = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            llm_provider="openai_realtime",
            llm_model="gpt-realtime-2",
        )

        llm = next(line for line in estimate.lines if line.component == "llm")
        assert (
            llm.units_per_minute == REALTIME_TOKENS_PER_MINUTE["decibylopenairealtime"]
        )
        assert llm.units_per_minute != DEFAULT_TOKENS_PER_MINUTE
