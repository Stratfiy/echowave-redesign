"""Seed realistic billing demo data so every dashboard screen can be viewed populated.

    python -m scripts.seed_billing_demo            # seed
    python -m scripts.seed_billing_demo --reset    # delete previously seeded data first

Creates several accounts across the three account types, a few thousand calls
spread over the last 30 days in a mix of languages, one outbound campaign, per-turn
latency metrics, provider rates, an effective-dated rate change, credit top-ups,
and the daily rollups the dashboard reads from.

Everything it writes is tagged with a ``seed-`` provider_id prefix so ``--reset``
can remove exactly the demo data and nothing else. It refuses to touch rows it
did not create.

The data is deliberately uneven — one account is loss-making, one has a low
credit balance, one has been silent for weeks, and Kannada calls are slower than
English ones — so the dashboard's warning states and the latency-by-language
breakdown have something real to show.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.constants import DATABASE_URL  # noqa: E402
from api.db.models import (  # noqa: E402
    CallCostItemModel,
    CallTurnMetricModel,
    CampaignModel,
    CreditLedgerModel,
    DailyOrganizationRollupModel,
    OrganizationModel,
    OrganizationRateHistoryModel,
    ProviderRateModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
    organization_users_association,
)
from api.enums import CostComponent, CreditLedgerKind, RateUnit  # noqa: E402
from api.services.billing.costing import cost_workflow_run  # noqa: E402
from api.services.billing.rollup import IST, refresh_daily_rollup  # noqa: E402

SEED_PREFIX = "seed-"
DAYS = 30
RNG_SEED = 20260729

# (slug, display name, account type, platform rate override, credit topup ₹, activity)
ACCOUNTS = [
    ("apollo-clinic", "Apollo Clinic Bengaluru", "clinic", None, 25_000, "healthy"),
    ("sunrise-dental", "Sunrise Dental", "clinic", None, 900, "low_credit"),
    ("reachwell-agency", "ReachWell Agency", "agency", 150_000, 60_000, "healthy"),
    ("thin-margin-co", "ThinMargin Outreach", "agency", 21_000, 40_000, "thin_margin"),
    (
        "northstar-ent",
        "Northstar Enterprise",
        "enterprise",
        120_000,
        200_000,
        "healthy",
    ),
    ("dormant-labs", "Dormant Labs", "clinic", None, 5_000, "dormant"),
]

LANGUAGES = ["en-IN", "hi-IN", "kn-IN", "ta-IN", "mr-IN"]

# Kannada and Tamil are genuinely slower through the pipeline than English.
# The dashboard's latency-by-language split exists to surface exactly this.
LANGUAGE_LATENCY_MS = {
    "en-IN": 620,
    "hi-IN": 700,
    "mr-IN": 760,
    "ta-IN": 880,
    "kn-IN": 1030,
}

DISPOSITIONS = ["XFER", "DNC", "CALLBACK", "NOT_INTERESTED", "COMPLETED"]

# (provider, model, component, unit, rate in millipaise).
# model "" is the provider-wide fallback that applies to any model without its
# own row; a model-specific row beats it. The spread inside one provider is the
# whole reason rates carry a model at all — gpt-4o against gpt-4o-mini is more
# than an order of magnitude.
PROVIDER_RATES = [
    ("deepgram", "", CostComponent.STT, RateUnit.MINUTE, 25_000),
    ("sarvam", "", CostComponent.STT, RateUnit.MINUTE, 18_000),
    ("openai", "", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 12_000),
    ("openai", "gpt-4o", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 12_000),
    ("openai", "gpt-4o-mini", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 700),
    ("openai", "gpt-4.1-mini", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 1_400),
    ("azure", "", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 11_000),
    ("azure", "gpt-4.1-mini", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 1_300),
    ("anthropic", "", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 14_000),
    ("sarvam", "", CostComponent.TTS, RateUnit.THOUSAND_CHARS, 40_000),
    ("elevenlabs", "", CostComponent.TTS, RateUnit.THOUSAND_CHARS, 90_000),
    ("elevenlabs", "turbo-v2", CostComponent.TTS, RateUnit.THOUSAND_CHARS, 45_000),
    ("twilio", "", CostComponent.TELEPHONY, RateUnit.MINUTE, 55_000),
    ("plivo", "", CostComponent.TELEPHONY, RateUnit.MINUTE, 42_000),
]


async def reset(session: AsyncSession) -> None:
    """Delete only rows this script created, identified by the seed prefix."""
    orgs = (
        await session.scalars(
            select(OrganizationModel).where(
                OrganizationModel.provider_id.like(f"{SEED_PREFIX}%")
            )
        )
    ).all()
    org_ids = [o.id for o in orgs]

    if org_ids:
        workflow_ids = (
            await session.scalars(
                select(WorkflowModel.id).where(
                    WorkflowModel.organization_id.in_(org_ids)
                )
            )
        ).all()
        if workflow_ids:
            run_ids = (
                await session.scalars(
                    select(WorkflowRunModel.id).where(
                        WorkflowRunModel.workflow_id.in_(workflow_ids)
                    )
                )
            ).all()
            if run_ids:
                await session.execute(
                    delete(CallCostItemModel).where(
                        CallCostItemModel.workflow_run_id.in_(run_ids)
                    )
                )
                await session.execute(
                    delete(CallTurnMetricModel).where(
                        CallTurnMetricModel.workflow_run_id.in_(run_ids)
                    )
                )
                await session.execute(
                    delete(WorkflowRunModel).where(WorkflowRunModel.id.in_(run_ids))
                )
            await session.execute(
                delete(CampaignModel).where(CampaignModel.workflow_id.in_(workflow_ids))
            )
            await session.execute(
                delete(WorkflowModel).where(WorkflowModel.id.in_(workflow_ids))
            )

        for model in (
            CreditLedgerModel,
            DailyOrganizationRollupModel,
            OrganizationRateHistoryModel,
        ):
            await session.execute(
                delete(model).where(model.organization_id.in_(org_ids))
            )
        # The membership rows first: the association table's FK has no cascade,
        # so deleting the organizations while a staff user is still attached
        # fails the whole reset.
        await session.execute(
            organization_users_association.delete().where(
                organization_users_association.c.organization_id.in_(org_ids)
            )
        )
        await session.execute(
            delete(OrganizationModel).where(OrganizationModel.id.in_(org_ids))
        )

    await session.execute(
        delete(UserModel).where(UserModel.provider_id.like(f"{SEED_PREFIX}%"))
    )
    # Provider rates are global, not per-account; only remove the demo ones.
    await session.execute(
        delete(ProviderRateModel).where(ProviderRateModel.note == "seed demo rate")
    )
    await session.commit()
    print(f"Reset: removed {len(org_ids)} seeded account(s) and their data.")


async def seed(session: AsyncSession) -> None:
    rng = random.Random(RNG_SEED)  # deterministic: same demo data every run
    now = datetime.now(UTC)
    start = now - timedelta(days=DAYS)

    existing = await session.scalar(
        select(OrganizationModel).where(
            OrganizationModel.provider_id.like(f"{SEED_PREFIX}%")
        )
    )
    if existing is not None:
        print("Seed data already present. Re-run with --reset to rebuild.")
        return

    for provider, model, component, unit, rate in PROVIDER_RATES:
        session.add(
            ProviderRateModel(
                provider=provider,
                model=model,
                component=component.value,
                unit=unit.value,
                rate_mpaise=rate,
                effective_from=start - timedelta(days=1),
                note="seed demo rate",
            )
        )

    staff = UserModel(
        provider_id=f"{SEED_PREFIX}staff",
        email="ops@decibyl.example",
        is_superuser=True,
    )
    session.add(staff)
    await session.flush()

    total_runs = 0

    for slug, name, account_type, rate_override, topup_rupees, behaviour in ACCOUNTS:
        org = OrganizationModel(
            provider_id=f"{SEED_PREFIX}{slug}",
            quota_decibyl_tokens=0,
            billing_name=name,
            account_type=account_type,
            billing_currency="INR",
            billing_status="active",
            platform_rate_mpaise=rate_override,
        )
        session.add(org)
        await session.flush()
        # Explicit insert rather than org.users.append(): appending to the
        # relationship lazy-loads it, which raises MissingGreenlet on an
        # async session.
        await session.execute(
            organization_users_association.insert().values(
                user_id=staff.id, organization_id=org.id
            )
        )

        if rate_override is not None:
            # An effective-dated change part-way through the window, so the
            # rate-history panel and historical re-costing have real data.
            session.add_all(
                [
                    OrganizationRateHistoryModel(
                        organization_id=org.id,
                        platform_rate_mpaise=rate_override + 30_000,
                        effective_from=start - timedelta(days=1),
                        effective_to=start + timedelta(days=DAYS // 2),
                        set_by=staff.id,
                        note="Launch pricing",
                    ),
                    OrganizationRateHistoryModel(
                        organization_id=org.id,
                        platform_rate_mpaise=rate_override,
                        effective_from=start + timedelta(days=DAYS // 2),
                        set_by=staff.id,
                        note="Negotiated volume rate",
                    ),
                ]
            )

        session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=topup_rupees * 100,
                kind=CreditLedgerKind.TOPUP.value,
                ref_type="seed",
                ref_id=slug,
                balance_after_paise=topup_rupees * 100,
                note="Seed top-up",
                created_by=staff.id,
            )
        )

        workflow = WorkflowModel(
            name=f"{name} — Reception Agent",
            user_id=staff.id,
            organization_id=org.id,
            workflow_definition={},
            template_context_variables={},
            call_disposition_codes={},
        )
        session.add(workflow)
        await session.flush()

        campaign = None
        if account_type == "agency":
            campaign = CampaignModel(
                name=f"{name} — Q3 Outreach",
                organization_id=org.id,
                workflow_id=workflow.id,
                created_by=staff.id,
                source_type="csv",
                source_id=f"{slug}-contacts.csv",
                state="running",
                total_rows=1200,
                processed_rows=940,
                started_at=start + timedelta(days=3),
            )
            session.add(campaign)
            await session.flush()

        # Dormant accounts stop early so the "no activity in 14 days" flag fires.
        active_days = 8 if behaviour == "dormant" else DAYS
        calls_per_day = {
            "healthy": (18, 40),
            "low_credit": (4, 10),
            "thin_margin": (25, 50),
            "dormant": (3, 8),
        }[behaviour]

        for day_offset in range(active_days):
            day_start = start + timedelta(days=day_offset)
            for _ in range(rng.randint(*calls_per_day)):
                run = _make_run(
                    rng,
                    workflow=workflow,
                    campaign=campaign,
                    day_start=day_start,
                    behaviour=behaviour,
                )
                session.add(run)
                await session.flush()
                _add_turn_metrics(session, rng, run)
                total_runs += 1

        await session.flush()
        print(f"  seeded {name}")

    await session.commit()

    # Cost every run, then build the rollups the dashboard reads.
    run_ids = (
        await session.scalars(
            select(WorkflowRunModel.id)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .join(
                OrganizationModel, WorkflowModel.organization_id == OrganizationModel.id
            )
            .where(OrganizationModel.provider_id.like(f"{SEED_PREFIX}%"))
        )
    ).all()
    print(f"Costing {len(run_ids)} calls...")
    for index, run_id in enumerate(run_ids, 1):
        await cost_workflow_run(session, run_id)
        if index % 500 == 0:
            await session.commit()
            print(f"  costed {index}/{len(run_ids)}")
    await session.commit()

    print("Building daily rollups...")
    today_ist = datetime.now(IST).date()
    for offset in range(DAYS + 1):
        await refresh_daily_rollup(session, day=today_ist - timedelta(days=offset))
    await session.commit()

    print(f"\nDone: {len(ACCOUNTS)} accounts, {total_runs} calls, {DAYS} days.")


def _make_run(rng, *, workflow, campaign, day_start, behaviour) -> WorkflowRunModel:
    started = day_start + timedelta(
        hours=rng.randint(9, 20), minutes=rng.randint(0, 59)
    )
    language = rng.choices(LANGUAGES, weights=[45, 25, 15, 10, 5])[0]

    answered = rng.random() < 0.72
    completed = answered and rng.random() < 0.78
    duration = rng.randint(25, 420) if answered else 0

    # ThinMargin runs long, chatty calls on a low platform rate, so its provider
    # pass-through eats most of the fee — the margin warning has something to flag.
    token_rate = 90 if behaviour == "thin_margin" else 35
    char_rate = 60 if behaviour == "thin_margin" else 22

    # Processor keys carry the "#N" instance suffix pipecat really emits, so the
    # seeded data exercises the same parsing path as a production call.
    usage_info = {"call_duration_seconds": duration}
    if duration:
        usage_info["llm"] = {
            "OpenAILLMService#0|||gpt-4o-mini": {
                "prompt_tokens": duration * token_rate,
                "completion_tokens": duration * (token_rate // 3),
                "total_tokens": duration * token_rate,
            }
        }
        usage_info["tts"] = {"SarvamTTSService#0|||bulbul": duration * char_rate}
        usage_info["stt"] = {"DeepgramSTTService#0|||nova-3": duration}
        usage_info["telephony"] = {"twilio": duration}

    return WorkflowRunModel(
        name=f"call-{rng.randint(100000, 999999)}",
        workflow_id=workflow.id,
        campaign_id=campaign.id if campaign else None,
        mode="twilio",
        call_type="outbound" if campaign else "inbound",
        state="completed",
        is_completed=completed,
        language=language,
        billable_seconds=duration,
        created_at=started,
        answered_at=started + timedelta(seconds=rng.randint(3, 12))
        if answered
        else None,
        ended_at=started + timedelta(seconds=duration + 5),
        usage_info=usage_info,
        cost_info={},
        initial_context={
            "caller_number": f"+9198{rng.randint(10000000, 99999999)}",
            "called_number": f"+9180{rng.randint(1000000, 9999999)}",
        },
        gathered_context={
            "mapped_call_disposition": rng.choice(DISPOSITIONS)
            if answered
            else "NO_ANSWER"
        },
    )


def _add_turn_metrics(session, rng, run) -> None:
    """Per-turn latency, skewed by language so the split is visible."""
    if not run.billable_seconds:
        return
    base = LANGUAGE_LATENCY_MS.get(run.language, 700)
    for turn_index in range(rng.randint(2, 9)):
        # Long tail: a few turns are much slower, which is what the p95 line and
        # the "slowest 20 turns" table are for.
        latency = int(rng.gauss(base, base * 0.22))
        if rng.random() < 0.05:
            latency = int(latency * rng.uniform(2.0, 3.5))
        latency = max(latency, 180)

        endpoint = rng.randint(90, 260)
        stt_final = endpoint + rng.randint(60, 200)
        llm_first = stt_final + rng.randint(90, 320)
        tts_first = llm_first + rng.randint(60, 240)

        session.add(
            CallTurnMetricModel(
                workflow_run_id=run.id,
                turn_index=turn_index,
                t_user_stopped_ms=0,
                t_endpoint_fired_ms=endpoint,
                t_stt_final_ms=stt_final,
                t_llm_first_token_ms=llm_first,
                t_tts_first_byte_ms=tts_first,
                t_audio_out_ms=latency,
                latency_ms=latency,
                tool_called="lookup_patient" if rng.random() < 0.15 else None,
                tool_ms=rng.randint(120, 900) if rng.random() < 0.15 else None,
                created_at=run.created_at,
            )
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete previously seeded data before seeding.",
    )
    args = parser.parse_args()

    engine = create_async_engine(DATABASE_URL, echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        if args.reset:
            await reset(session)
        await seed(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
