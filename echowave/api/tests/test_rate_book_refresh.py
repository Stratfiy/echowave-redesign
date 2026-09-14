"""The rate book after the survey of 14 Sept 2026 (KAN-58).

Every row names where it was read and when; the figures the survey found
wrong are corrected; the financial model's voice minute reconciles to the
book; nothing defaults to a model that is retiring; and a card seeded from an
older book can take the new one without losing a rate somebody chose.
"""

from __future__ import annotations

import re

import pytest

from api import constants
from api.enums import CostComponent, RateUnit
from api.services.billing import seed_rates
from api.services.billing.default_rates import (
    AS_OF,
    CHECKED_ON,
    DEFAULT_RATES,
    PROVIDER_SOURCES,
    REFERENCE_USD_INR,
    SEED_NOTE,
    SEED_NOTE_PREFIX,
    is_seeded_note,
    usd_to_mpaise,
)
from api.services.billing.estimator import DEFAULT_TOKENS_PER_MINUTE
from api.services.configuration import managed_tiers

ISO_DAY_OR_MONTH = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _row(provider: str, component: CostComponent, model: str = ""):
    return next(
        r
        for r in DEFAULT_RATES
        if r.provider == provider and r.component == component and r.model == model
    )


def _rupees(rate) -> float:
    """A row's rupee price per unit at the reference exchange rate."""
    return usd_to_mpaise(rate.usd_per_unit, usd_inr=REFERENCE_USD_INR) / 100_000


class TestEveryRowHasAProvenance:
    def test_every_row_names_a_page_and_a_date(self):
        for rate in DEFAULT_RATES:
            assert rate.source.startswith("https://"), (
                f"{rate.provider}/{rate.model or '(any)'} has no source page"
            )
            assert ISO_DAY_OR_MONTH.match(rate.source_checked_on), (
                f"{rate.provider}/{rate.model or '(any)'} has no check date"
            )

    def test_every_provider_in_the_book_has_a_page(self):
        assert {r.provider for r in DEFAULT_RATES} <= set(PROVIDER_SOURCES)

    def test_the_book_is_dated_to_the_survey(self):
        assert AS_OF == CHECKED_ON == "2026-09-14"
        assert CHECKED_ON in SEED_NOTE

    def test_the_rows_the_survey_named_were_re_read_that_day(self):
        for provider, component, model in [
            ("sarvam", CostComponent.STT, ""),
            ("sarvam", CostComponent.TTS, ""),
            ("sarvam", CostComponent.LLM, "sarvam-105b"),
            ("deepgram", CostComponent.STT, ""),
            ("assemblyai", CostComponent.STT, ""),
            ("smallest", CostComponent.TTS, ""),
            ("cartesia", CostComponent.TTS, ""),
            ("elevenlabs", CostComponent.TTS, "eleven_flash_v2_5"),
            ("twilio", CostComponent.TELEPHONY, ""),
            ("plivo", CostComponent.TELEPHONY, ""),
            ("google", CostComponent.LLM, "gemini-2.5-flash-lite"),
            ("anthropic", CostComponent.LLM, "claude-sonnet-5"),
        ]:
            assert _row(provider, component, model).source_checked_on == CHECKED_ON

    def test_a_seeded_note_is_recognisable_and_a_chosen_one_is_not(self):
        assert is_seeded_note(SEED_NOTE)
        assert is_seeded_note(f"{SEED_NOTE_PREFIX} — anything after")
        assert not is_seeded_note("Contracted with Smallest, Sept 2026")
        assert not is_seeded_note(None)
        assert not is_seeded_note("")


class TestTheFiguresTheSurveyFoundWrong:
    def test_sarvam_tts_fallback_is_bulbul_v3_at_three_rupees(self):
        """The 'rate book had ₹1.50' in KAN-58: the fallback quoted v2 after
        the default managed tier had moved to v3."""
        assert managed_tiers._defaults()[("tts", "default")].model == "bulbul:v3"
        assert _rupees(_row("sarvam", CostComponent.TTS)) == pytest.approx(3.00)
        assert _rupees(_row("sarvam", CostComponent.TTS, "bulbul:v3")) == pytest.approx(
            3.00
        )

    def test_sarvam_105b_is_the_published_blend(self):
        """₹29.28 in / ₹73.20 out per 1M, blended 70/30 → ₹42.456 per 1M."""
        per_1k = _rupees(_row("sarvam", CostComponent.LLM, "sarvam-105b"))
        assert per_1k == pytest.approx(0.042456, abs=1e-5)
        assert _rupees(_row("sarvam", CostComponent.LLM)) == pytest.approx(per_1k)
        assert _rupees(
            _row("sarvam", CostComponent.LLM, "sarvam-105b-conversations")
        ) == pytest.approx(per_1k)

    def test_gemma_4_is_priced_and_provisional(self):
        row = _row("sarvam", CostComponent.LLM, "gemma-4-31b")
        assert row.provisional
        assert "PROVISIONAL" in row.basis
        assert _rupees(row) == pytest.approx(0.05307, abs=1e-5)

    def test_deepgram_is_the_multilingual_streaming_page_price(self):
        assert _row("deepgram", CostComponent.STT).usd_per_unit == 0.0058

    def test_assemblyai_has_a_row_at_fifteen_cents_an_hour(self):
        row = _row("assemblyai", CostComponent.STT)
        assert row.unit == RateUnit.MINUTE
        assert row.usd_per_unit == pytest.approx(0.0025)

    def test_twilio_india_is_the_page_price_in_dollars(self):
        """$0.0496 to a mobile — not the Rs1.20 the book carried, and not the
        $0.0075 in the survey, which is the US rate."""
        assert _row("twilio", CostComponent.TELEPHONY).usd_per_unit == 0.0496

    def test_cartesia_is_provisional_until_an_invoice_says_otherwise(self):
        assert _row("cartesia", CostComponent.TTS).provisional

    def test_smallest_is_the_page_price_not_the_survey_figure(self):
        assert _row("smallest", CostComponent.TTS).usd_per_unit == 0.0175
        assert (
            _row("smallest", CostComponent.TTS, "lightning_v3.1_pro").usd_per_unit
            == 0.0195
        )

    def test_the_current_flash_is_priced_with_its_rise_noted(self):
        row = _row("google", CostComponent.LLM, "gemini-3.8-flash")
        assert row.usd_per_unit == pytest.approx((0.75 * 0.7 + 3.75 * 0.3) / 1000)
        assert "2027" in row.basis


class TestNothingDefaultsToARetiringModel:
    RETIRING = "gemini-2.5-flash-lite"

    def test_the_row_stays_for_history_but_says_so(self):
        assert "historical" in _row("google", CostComponent.LLM, self.RETIRING).basis

    def test_no_managed_tier_resolves_to_it(self):
        for (component, tier), upstream in managed_tiers._defaults().items():
            assert upstream.model != self.RETIRING, (component, tier)

    def test_no_builder_default_is_it(self):
        assert self.RETIRING not in constants.AGENT_BUILDER_MODELS.values()


class TestTheBuilderBuildsOnSonnet5:
    def test_the_default(self):
        assert constants.AGENT_BUILDER_MODELS["anthropic"] == "claude-sonnet-5"

    def test_both_the_new_and_the_old_default_are_priced(self):
        assert (
            _row("anthropic", CostComponent.LLM, "claude-sonnet-5").usd_per_unit
            < _row("anthropic", CostComponent.LLM, "claude-sonnet-4-5").usd_per_unit
        )


class TestTheFinancialModelReconcilesToTheBook:
    """KAN-58's acceptance: the model's voice minute — ₹3.71 at list, ₹2.78
    after Sarvam's 30% — must come out of the book, not sit beside it.

    The stack is the Everyday tier: Sarvam STT, Bulbul v3, gpt-4.1-mini at
    the estimator's tokens a minute, Plivo. Spoken characters a minute is the
    model's 850 (the study's §1 warns the estimator assumes fewer)."""

    CHARACTERS_PER_MINUTE = 850
    SARVAM_DISCOUNT = 0.30

    def _lines(self) -> dict[str, float]:
        return {
            "stt": _rupees(_row("sarvam", CostComponent.STT, "saaras:v3")),
            "tts": _rupees(_row("sarvam", CostComponent.TTS, "bulbul:v3"))
            * self.CHARACTERS_PER_MINUTE
            / 1000,
            "llm": _rupees(_row("openai", CostComponent.LLM, "gpt-4.1-mini"))
            * DEFAULT_TOKENS_PER_MINUTE
            / 1000,
            "carriage": _rupees(_row("plivo", CostComponent.TELEPHONY, "outbound")),
        }

    def test_the_list_minute(self):
        assert sum(self._lines().values()) == pytest.approx(3.71, abs=0.05)

    def test_the_minute_after_sarvams_discount(self):
        lines = self._lines()
        discounted = (lines["stt"] + lines["tts"]) * (1 - self.SARVAM_DISCOUNT)
        assert discounted + lines["llm"] + lines["carriage"] == pytest.approx(
            2.78, abs=0.05
        )


@pytest.mark.asyncio
class TestRefreshingASeededCard:
    """A production card seeded from the August book, with one contracted
    rate typed by a person, takes the September book without losing it."""

    async def _seed_old_card(self, session):
        from datetime import UTC, datetime, timedelta

        from api.services.billing.rate_card import set_provider_rate

        long_ago = datetime.now(UTC) - timedelta(days=30)
        # The August book's Sarvam TTS fallback, seeded.
        await set_provider_rate(
            session,
            actor_user_id=None,
            provider="sarvam",
            component=CostComponent.TTS,
            unit=RateUnit.THOUSAND_CHARS,
            rate_mpaise=150_000,
            effective_from=long_ago,
            note="Seeded default — list price as of 2026-08. (Bulbul v2)",
        )
        # A contracted Smallest rate, typed by an operator.
        await set_provider_rate(
            session,
            actor_user_id=None,
            provider="smallest",
            component=CostComponent.TTS,
            unit=RateUnit.THOUSAND_CHARS,
            rate_mpaise=120_000,
            effective_from=long_ago,
            note="Contracted, Sept 2026",
        )
        await session.flush()

    async def test_the_plan_names_each_rows_fate(self, db_session, async_session):
        await self._seed_old_card(async_session)
        plan = await seed_rates.plan(async_session, refresh_seeded=True)
        by = {
            (l.rate.provider, l.rate.model, l.rate.component.value): l
            for l in plan.lines
        }
        assert by[("sarvam", "", "tts")].action == seed_rates.ACTION_REPLACE
        assert by[("smallest", "", "tts")].action == seed_rates.ACTION_SKIP_CHOSEN
        assert by[("assemblyai", "", "stt")].action == seed_rates.ACTION_WRITE

    async def test_without_the_flag_a_seeded_row_is_left_alone(
        self, db_session, async_session
    ):
        await self._seed_old_card(async_session)
        plan = await seed_rates.plan(async_session)
        by = {
            (l.rate.provider, l.rate.model, l.rate.component.value): l
            for l in plan.lines
        }
        assert by[("sarvam", "", "tts")].action == seed_rates.ACTION_SKIP_SEEDED

    async def test_applying_replaces_the_seeded_row_and_keeps_the_chosen_one(
        self, db_session, async_session
    ):
        from sqlalchemy import select

        from api.db.models import ProviderRateModel

        await self._seed_old_card(async_session)
        await seed_rates.apply(async_session, actor_user_id=None, refresh_seeded=True)
        await async_session.flush()

        open_rows = {
            (r.provider, r.model, r.component): r
            for r in (
                await async_session.scalars(
                    select(ProviderRateModel).where(
                        ProviderRateModel.effective_to.is_(None)
                    )
                )
            ).all()
        }
        sarvam = open_rows[("sarvam", "", "tts")]
        assert sarvam.rate_mpaise == 300_000
        assert sarvam.source_url == PROVIDER_SOURCES["sarvam"]
        assert sarvam.source_checked_on == CHECKED_ON
        assert is_seeded_note(sarvam.note)

        smallest = open_rows[("smallest", "", "tts")]
        assert smallest.rate_mpaise == 120_000
        assert smallest.note == "Contracted, Sept 2026"

        assert open_rows[("assemblyai", "", "stt")].source_checked_on == CHECKED_ON


@pytest.mark.asyncio
class TestTheScreensReadIt:
    """The rate card returns what the panels read, and the seed endpoint
    plans before it writes."""

    def _client(self, user):
        from contextlib import asynccontextmanager

        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import get_superuser

        @asynccontextmanager
        async def _ctx():
            async def _override():
                return user

            app.dependency_overrides[get_superuser] = _override
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    yield client
            finally:
                app.dependency_overrides.pop(get_superuser, None)

        return _ctx()

    async def _staff(self, session):
        from api.db.models import UserModel
        from api.enums import StaffRole

        user = UserModel(
            provider_id="staff-rate-book", staff_role=StaffRole.SUPERADMIN.value
        )
        session.add(user)
        await session.flush()
        return user

    async def test_the_rate_card_carries_multipliers_bundles_and_provenance(
        self, db_session, async_session
    ):
        """The multipliers panel (KAN-54) read fields the route never
        returned, so it stayed hidden on production. Now it cannot."""
        from api.services.billing.rate_card import set_provider_rate

        await set_provider_rate(
            async_session,
            actor_user_id=None,
            provider="deepgram",
            component=CostComponent.STT,
            unit=RateUnit.MINUTE,
            rate_micros_usd=5_800,
            note=SEED_NOTE,
            source_url=PROVIDER_SOURCES["deepgram"],
            source_checked_on=CHECKED_ON,
        )
        await async_session.commit()
        staff = await self._staff(async_session)
        await async_session.commit()

        async with self._client(staff) as client:
            response = await client.get("/api/v1/admin/billing/rate-card")
        assert response.status_code == 200, response.text
        body = response.json()
        assert {m["component"] for m in body["component_multipliers"]} >= {
            "llm",
            "tts",
            "stt",
            "telephony",
        }
        assert isinstance(body["bundles"], list)
        row = next(r for r in body["provider_rates"] if r["provider"] == "deepgram")
        assert row["source_url"] == PROVIDER_SOURCES["deepgram"]
        assert row["source_checked_on"] == CHECKED_ON
        assert row["is_seeded"] is True

    async def test_the_seed_endpoint_plans_first_and_writes_on_request(
        self, db_session, async_session
    ):
        from sqlalchemy import func, select

        from api.db.models import ProviderRateModel

        staff = await self._staff(async_session)
        await async_session.commit()

        async with self._client(staff) as client:
            preview = await client.post(
                "/api/v1/admin/billing/rate-card/seed",
                json={"refresh_seeded": True, "dry_run": True},
            )
            assert preview.status_code == 200, preview.text
            assert preview.json()["written"] == 0
            assert preview.json()["would_write"] == len(DEFAULT_RATES)
            assert all(
                line["source_url"].startswith("https://")
                for line in preview.json()["lines"]
            )

            applied = await client.post(
                "/api/v1/admin/billing/rate-card/seed",
                json={"refresh_seeded": True, "dry_run": False},
            )
            assert applied.status_code == 200, applied.text
            assert applied.json()["written"] == len(DEFAULT_RATES)

        open_rows = await async_session.scalar(
            select(func.count())
            .select_from(ProviderRateModel)
            .where(ProviderRateModel.effective_to.is_(None))
        )
        assert open_rows == len(DEFAULT_RATES)
