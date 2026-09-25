"""D-1: the charge rule of 25 September 2026, behind CHARGE_RULE_2026_09_ENABLED.

Off, every charge is what it was (the rest of the suite holds that). On, the
exchange table in ``billing/exchange.py`` is the price list: the figures,
the multipliers they are set against, the token allowance and the model
line on the same ledger row, reads and writes into systems of record, the
WhatsApp categories, the one voice rate, transcription, and the rate card
the app shows.
"""

from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api import constants
from api.db.models import CreditLedgerModel, OrganizationModel
from api.services.billing import default_rates, events, exchange
from api.services.billing.credits import PAISE_PER_CREDIT


@pytest.fixture
def rule_on(monkeypatch):
    monkeypatch.setattr(constants, "CHARGE_RULE_2026_09_ENABLED", True)


@pytest.fixture
def rule_off(monkeypatch):
    monkeypatch.setattr(constants, "CHARGE_RULE_2026_09_ENABLED", False)


class TestTheTable:
    def test_the_published_figures(self):
        assert exchange.VERSION == "2026-09-25"
        assert exchange.EFFECTIVE_FROM.isoformat() == "2026-09-25"
        assert exchange.CREDITS == {
            "text_reply": 1,
            "knowledge_answer": 1,
            "tool_call": 1,
            "tool_call_premium": 2,
            "trigger_run": 1,
            "task_run": 1,
            "routine_run": 2,
            "script_run": 4,
            "translation": 1,
            "number_verification": 2,
            "builder_message": 5,
            "whatsapp_utility": 1,
            "whatsapp_authentication": 1,
            "whatsapp_marketing": 3,
            "whatsapp_service_reply": 0,
            "voice_minute": 12,
            "voice_minute_premium": 18,
            "transcription_minute": 2,
        }
        assert exchange.TABLE_BY_KEY["premium_model_tokens"].credits is None
        assert exchange.M_COMPUTE == 3.4
        assert exchange.M_PASS_THROUGH == 1.7
        assert exchange.PREMIUM_VOICE_PROVIDERS == frozenset({"elevenlabs"})
        assert PAISE_PER_CREDIT == 50

    def test_credits_for_cost_is_ceil_of_cost_times_m_over_fifty(self):
        assert exchange.credits_for_cost(0, 3.4) == 0
        assert exchange.credits_for_cost(-5, 3.4) == 0
        assert exchange.credits_for_cost(1, 3.4) == 1
        # 50 / 3.4 = 14.705...: exactly 14.7 is inside one credit, 15 is not.
        assert exchange.credits_for_cost(14.7, 3.4) == 1
        assert exchange.credits_for_cost(15, 3.4) == 2
        # Exact, no float noise: 86.31 x 1.7 = 146.727 -> 3 credits.
        assert exchange.credits_for_cost(86.31, 1.7) == 3
        assert exchange.credits_for_cost(Fraction(1, 3), 3.4) == 1

    def test_every_shipped_figure_reads_from_the_table(self, rule_on):
        """When the flag is on, the figure every charging path uses is the
        table's -- a test, so the two cannot drift."""
        from api.services.billing import messaging_charges

        for event in (
            events.TEXT_REPLY,
            events.KNOWLEDGE_ANSWER,
            events.ROUTINE_RUN,
            events.TRIGGER_RUN,
            events.TASK_RUN,
            events.SCRIPT_RUN,
            events.TOOL_CALL,
            events.TOOL_CALL_PREMIUM,
            events.NUMBER_VERIFICATION,
            events.TRANSLATION,
            events.TRANSCRIPTION,
        ):
            assert events.credits_for(event) == exchange.CREDITS[event], event
        for category, key in exchange.WHATSAPP_CATEGORIES.items():
            assert (
                messaging_charges.price_paise(category)
                == exchange.CREDITS[key] * PAISE_PER_CREDIT
            ), category
        assert exchange.voice_credits_per_minute(["sarvam"]) == 12
        assert exchange.voice_credits_per_minute(["elevenlabs"]) == 18

    def test_the_builder_fee_is_still_retired_by_the_ladder(self, rule_on, monkeypatch):
        monkeypatch.setattr(constants, "PLAN_LADDER_2026_09_ENABLED", True)
        assert events.credits_for(events.BUILDER_MESSAGE) == 0
        monkeypatch.setattr(constants, "PLAN_LADDER_2026_09_ENABLED", False)
        assert events.credits_for(events.BUILDER_MESSAGE) == 5

    def test_the_flag_off_figures_are_the_old_table(self, rule_off):
        for event, credits in events.EVENT_CREDITS.items():
            if event == events.BUILDER_MESSAGE:
                continue
            assert events.credits_for(event) == credits
        assert events.credits_for(events.KNOWLEDGE_ANSWER) == 2
        assert events.credits_for(events.TOOL_CALL_PREMIUM) == 3


class TestTheMargins:
    """Each line with a known cost floor clears it at its multiple."""

    def test_whatsapp_marketing_covers_meta_at_pass_through(self):
        assert (
            exchange.CREDITS["whatsapp_marketing"] * PAISE_PER_CREDIT
            >= exchange.META_MARKETING_TEMPLATE_PAISE * exchange.M_PASS_THROUGH
        )

    def test_whatsapp_utility_covers_meta_at_pass_through(self):
        for key in ("whatsapp_utility", "whatsapp_authentication"):
            assert (
                exchange.CREDITS[key] * PAISE_PER_CREDIT
                >= exchange.META_UTILITY_TEMPLATE_PAISE * exchange.M_PASS_THROUGH
            )

    def test_transcription_covers_the_batch_stt_minute(self):
        """The managed default (Sarvam saaras:v3, from the book) and
        Deepgram's pre-recorded price: the two a file is transcribed on."""
        from api.services.configuration import managed_tiers

        default = managed_tiers.resolve("stt", "default")
        sarvam = next(
            r
            for r in default_rates.STT_RATES
            if (r.provider, r.model) == (default.provider, default.model)
        )
        for usd in (sarvam.usd_per_unit, exchange.DEEPGRAM_BATCH_USD_PER_MINUTE):
            paise = usd * default_rates.REFERENCE_USD_INR * 100
            assert (
                exchange.CREDITS["transcription_minute"] * PAISE_PER_CREDIT
                >= paise * exchange.M_PASS_THROUGH
            ), usd

    def test_a_voice_minute_covers_the_measured_managed_stack(self):
        assert (
            exchange.CREDITS["voice_minute"] * PAISE_PER_CREDIT
            >= exchange.VOICE_MANAGED_STACK_PAISE_PER_MINUTE * 2
        )

    def test_a_premium_voice_minute_covers_elevenlabs_on_top(self):
        elevenlabs = next(
            r
            for r in default_rates.TTS_RATES
            if r.provider == "elevenlabs" and r.model == ""
        )
        # 900 characters a minute, the pricing study's upper Indic figure.
        tts_paise = (
            elevenlabs.usd_per_unit * 0.9 * default_rates.REFERENCE_USD_INR * 100
        )
        assert (
            exchange.CREDITS["voice_minute_premium"] * PAISE_PER_CREDIT
            >= exchange.VOICE_MANAGED_STACK_PAISE_PER_MINUTE + tts_paise
        )

    def test_the_allowance_is_what_a_credit_pays_for(self):
        """Each credit includes the model cost it covers at M_COMPUTE, and
        no more: that is what holds the 65% floor on every model."""
        assert (
            exchange.INCLUDED_MODEL_PAISE_PER_CREDIT * Fraction(str(exchange.M_COMPUTE))
            == PAISE_PER_CREDIT
        )

    def test_a_real_agent_turn_on_the_cheapest_model_is_one_credit(self):
        """The case that sank the token allowance: a 16k-token turn (system
        prompt, tools, memory) on gpt-5-nano costs about 6 paise and must
        stay a 1-credit reply."""
        assert (
            exchange.model_line_credits(
                [exchange.ModelTokens("gpt-5-nano", 16_000, Fraction(6))]
            )
            == 0
        )


class TestReadsAndWrites:
    @pytest.mark.parametrize(
        "name",
        [
            "HUBSPOT_CREATE_CONTACT",
            "salesforce_update_lead",
            "ZENDESK_DELETE_TICKET",
            "GMAIL_SEND_EMAIL",
            "slack_post_message",
            "shopify_add_product",
            "tally_insert_voucher",
            "zoho_crm_upsert_record",
            "SHOPIFY_CANCEL_ORDER",
            "RAZORPAY_REFUND_PAYMENT",
            "stripe_charge_customer",
            "zoho_invoice_issue_invoice",
            "freshdesk_set_status",
            "hubspot_patch_deal",
            "createInvoice",
            "salesforce.update-lead",
        ],
    )
    def test_a_write(self, name):
        assert exchange.is_write_action(name)

    @pytest.mark.parametrize(
        "name",
        [
            "HUBSPOT_GET_CONTACT",
            "salesforce_list_leads",
            "zendesk_search_tickets",
            "razorpay_fetch_refund",
            "ZENDESK_GET_ISSUE",
            "stripe_retrieve_charge",
            "settings",
            "reset_view",
            "",
            None,
            "hubspot_contacts",
            "do_the_thing",
        ],
    )
    def test_not_a_write(self, name):
        assert not exchange.is_write_action(name)

    def test_only_a_write_into_a_system_of_record_is_premium(
        self, rule_on, monkeypatch
    ):
        monkeypatch.setattr(events, "PREMIUM_CONNECTORS", frozenset({"hubspot"}))
        assert (
            events.tool_call_event("hubspot", "HUBSPOT_CREATE_CONTACT")
            == events.TOOL_CALL_PREMIUM
        )
        assert (
            events.tool_call_event("hubspot", "HUBSPOT_GET_CONTACT") == events.TOOL_CALL
        )
        assert events.tool_call_event("hubspot") == events.TOOL_CALL
        assert events.tool_call_event("gmail", "GMAIL_SEND_EMAIL") == events.TOOL_CALL

    def test_off_the_connector_alone_decides(self, rule_off, monkeypatch):
        monkeypatch.setattr(events, "PREMIUM_CONNECTORS", frozenset({"hubspot"}))
        assert (
            events.tool_call_event("hubspot", "HUBSPOT_GET_CONTACT")
            == events.TOOL_CALL_PREMIUM
        )


class TestTheModelLine:
    def _t(self, model, tokens, paise):
        return exchange.ModelTokens(model, tokens, Fraction(str(paise)))

    def test_cost_inside_the_credit_is_included(self):
        # 14 paise of model cost fits inside the 14.7 a credit covers.
        assert exchange.model_line_credits([self._t("gpt-4.1-mini", 6_000, 14)]) == 0

    def test_cost_past_the_credit_pays_the_excess(self):
        # 42 paise (a 16k turn on gpt-4.1-mini): 27.3 past, x3.4 = 92.8 -> 2.
        assert exchange.model_line_credits([self._t("gpt-4.1-mini", 16_000, 42)]) == 2

    def test_a_premium_reply(self):
        # 190 paise on gpt-5: 175.3 past, x3.4 = 596 -> 12, so 13 in all.
        assert exchange.model_line_credits([self._t("gpt-5", 5_000, 190)]) == 12

    def test_a_bigger_event_includes_more(self):
        # A routine is 2 credits, so it includes 29.4 paise.
        assert (
            exchange.model_line_credits(
                [self._t("gpt-4.1-mini", 10_000, 29)], event_credits=2
            )
            == 0
        )

    def test_one_rounding_across_models(self):
        assert (
            exchange.model_line_credits(
                [self._t("gpt-5", 100, 10), self._t("claude-sonnet-5", 100, 10)]
            )
            == 1
        )

    def test_nothing_recorded_is_nothing_charged(self):
        assert exchange.model_line_credits([]) == 0

    def test_standard_models_are_recognised(self):
        assert exchange.is_standard_model("gpt-4.1-mini-2025-04-14")
        assert exchange.is_standard_model("models/gemini-2.5-flash-lite")
        assert not exchange.is_standard_model("gpt-4.1")
        assert not exchange.is_standard_model("")


class TestTranscription:
    def test_two_a_minute_rounded_up_per_file_one_minimum(self):
        assert exchange.transcription_credits(0) == 1
        assert exchange.transcription_credits(None) == 1
        assert exchange.transcription_credits(10) == 2
        assert exchange.transcription_credits(60) == 2
        assert exchange.transcription_credits(61) == 4
        assert exchange.transcription_credits(600) == 20


class TestWhatsApp:
    def test_categories(self, rule_on):
        from api.services.billing import messaging_charges

        assert messaging_charges.price_paise() == 50
        assert messaging_charges.price_paise("utility") == 50
        assert messaging_charges.price_paise("authentication") == 50
        assert messaging_charges.price_paise("marketing") == 150
        assert messaging_charges.price_paise("service") == 0
        assert messaging_charges.price_paise("something-else") == 50

    def test_off_every_category_is_the_old_price(self, rule_off):
        from api.services.billing import messaging_charges

        for category in (None, "utility", "marketing", "service"):
            assert (
                messaging_charges.price_paise(category)
                == constants.WHATSAPP_MESSAGE_PRICE_PAISE
            )


class TestVoice:
    def test_one_rate_on_every_plan(self, rule_on):
        from api.services.billing import costing

        usage = {"tts": {"SarvamTTSService#0|||bulbul:v3": 900}}
        assert costing.charge_rule_voice_paise(usage) == 600
        assert costing.charge_rule_voice_paise(None) == 600

    def test_premium_voice(self, rule_on):
        from api.services.billing import costing

        usage = {"tts": {"ElevenLabsTTSService#0|||eleven_flash_v2_5": 900}}
        assert costing.charge_rule_voice_paise(usage) == 900


def _org_model(slug):
    return OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)


async def _org(session, slug: str) -> OrganizationModel:
    org = _org_model(slug)
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


def _usage(model="gpt-5", prompt=4_000, completion=1_000, key_source="managed"):
    return {
        "llm": {
            f"OpenAILLMService#0|||{model}": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
            }
        },
        "key_sources": {"llm": key_source},
    }


@pytest.mark.asyncio
class TestACharge:
    async def test_the_model_line_is_on_the_same_row(
        self, db_session, async_session, rule_on
    ):
        org = await _org(async_session, "rule-premium")
        with patch.object(
            exchange,
            "model_tokens_of",
            new=AsyncMock(
                return_value=[
                    exchange.ModelTokens("gpt-5", 5_000, Fraction(190)),
                ]
            ),
        ):
            paise = await events.charge(
                async_session,
                organization_id=org.id,
                event=events.TEXT_REPLY,
                ref_id="9:turn_a",
                note="Sales in chat",
                usage=[_usage()],
            )
        assert paise == 13 * 50
        rows = await _rows(async_session, org)
        assert len(rows) == 1
        assert rows[0].delta_paise == -650
        assert rows[0].note == "Text reply · 1 credit + 12 model · Sales in chat"

    async def test_a_knowledge_answer_is_one_credit(
        self, db_session, async_session, rule_on
    ):
        org = await _org(async_session, "rule-ka")
        paise = await events.charge(
            async_session,
            organization_id=org.id,
            event=events.KNOWLEDGE_ANSWER,
            ref_id="9:turn_k",
        )
        assert paise == 50
        rows = await _rows(async_session, org)
        assert rows[0].note == "Knowledge answer · 1 credit"

    async def test_byok_has_no_model_line(self, db_session, async_session, rule_on):
        org = await _org(async_session, "rule-byok")
        paise = await events.charge(
            async_session,
            organization_id=org.id,
            event=events.TEXT_REPLY,
            ref_id="9:turn_b",
            usage=[_usage(key_source="byok")],
        )
        assert paise == 50

    async def test_no_rate_on_file_charges_the_event_alone(
        self, db_session, async_session, rule_on
    ):
        org = await _org(async_session, "rule-norate")
        paise = await events.charge(
            async_session,
            organization_id=org.id,
            event=events.TEXT_REPLY,
            ref_id="9:turn_c",
            usage=[_usage(model="some-model-nobody-priced")],
        )
        assert paise == 50

    async def test_a_pricing_failure_never_fails_the_reply(
        self, db_session, async_session, rule_on
    ):
        org = await _org(async_session, "rule-boom")
        with patch.object(
            exchange, "model_tokens_of", new=AsyncMock(side_effect=RuntimeError("x"))
        ):
            paise = await events.charge(
                async_session,
                organization_id=org.id,
                event=events.TEXT_REPLY,
                ref_id="9:turn_d",
                usage=[_usage()],
            )
        assert paise == 50

    async def test_a_premium_model_is_priced_from_the_rate_book(
        self, db_session, async_session, rule_on
    ):
        from datetime import UTC, datetime

        from api.db.models import ProviderRateModel

        async_session.add(
            ProviderRateModel(
                provider="openai",
                component="llm",
                model="gpt-5",
                unit="1k_tokens",
                # 38 paise per 1k tokens: 5,000 tokens = 190 paise.
                rate_mpaise=38_000,
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        await async_session.flush()
        org = await _org(async_session, "rule-book")
        paise = await events.charge(
            async_session,
            organization_id=org.id,
            event=events.ROUTINE_RUN,
            ref_id="77",
            usage=[_usage()],
        )
        assert paise == (2 + 11) * 50

    async def test_off_the_usage_is_ignored(self, db_session, async_session, rule_off):
        org = await _org(async_session, "rule-off")
        with patch.object(exchange, "model_tokens_of", new=AsyncMock()) as priced:
            paise = await events.charge(
                async_session,
                organization_id=org.id,
                event=events.TEXT_REPLY,
                ref_id="9:turn_e",
                usage=[_usage()],
            )
        assert paise == 50
        priced.assert_not_awaited()
        rows = await _rows(async_session, org)
        assert rows[0].note == "Text reply · 1 credit"

    async def test_a_tool_call_carries_no_model_line(
        self, db_session, async_session, rule_on
    ):
        org = await _org(async_session, "rule-tool")
        with patch.object(exchange, "model_tokens_of", new=AsyncMock()) as priced:
            paise = await events.charge(
                async_session,
                organization_id=org.id,
                event=events.TOOL_CALL_PREMIUM,
                ref_id="9:c1",
                usage=[_usage()],
            )
        assert paise == 100
        priced.assert_not_awaited()

    async def test_transcription_is_a_metered_event(
        self, db_session, async_session, rule_on
    ):
        org = await _org(async_session, "rule-stt")
        paise = await events.charge(
            async_session,
            organization_id=org.id,
            event=events.TRANSCRIPTION,
            ref_id="upload:abc",
            credits=exchange.transcription_credits(125),
            note="3 min",
        )
        assert paise == 6 * 50
        rows = await _rows(async_session, org)
        assert rows[0].note == "Transcription · 6 credits · 3 min"


class TestTheEstimate:
    def test_on_the_estimate_prices_from_the_exchange(self, rule_on):
        from api.services.billing import run_estimate

        shape = run_estimate.Shape(
            connected_writes=2, premium_writes=2, is_routine=True
        )
        est = run_estimate.estimate(shape, items_per_run=1)
        by_what = {line.what: line for line in est.lines}
        assert by_what["Routine run"].credits_each == 2
        assert by_what["Text turns"].credits_each == 1
        assert by_what["Connected-app calls (a send each)"].credits_each == 2
        assert set(est.as_dict()) == {
            "items_per_run",
            "per_run_credits",
            "per_month_credits",
            "runs_per_month",
            "hard_maximum_per_run_credits",
            "lines",
            "hard_maximum",
            "notes",
        }

    def test_off_the_estimate_is_unchanged(self, rule_off):
        from api.services.billing import run_estimate

        shape = run_estimate.Shape(connected_writes=1, premium_writes=1)
        est = run_estimate.estimate(shape, items_per_run=1)
        by_what = {line.what: line for line in est.lines}
        assert by_what["Connected-app calls (a send each)"].credits_each == 3


class TestTheFlag:
    def test_it_is_registered(self):
        from api.services import features

        assert features.FLAGS["charge_rule"] == "CHARGE_RULE_2026_09_ENABLED"

    def test_it_defaults_off(self):
        # api/.env.test does not set it, so this is the default.
        assert constants.CHARGE_RULE_2026_09_ENABLED is False


@pytest.mark.asyncio
class TestThePicker:
    async def test_on_each_choice_says_what_a_reply_costs(self, rule_on):
        from api.services.configuration import chat_presets

        with patch(
            "api.services.configuration.platform_credentials.managed_providers",
            new=AsyncMock(return_value={"llm": ["openai"]}),
        ):
            menu = await chat_presets.menu(object())
        by_slug = {p["slug"]: p for p in menu["presets"]}
        assert by_slug["everyday"]["reply_credits"] == 1
        assert by_slug["smart"]["reply_credits"] == 1
        # Deep is GPT-4.1: $3.80/M blended, 51.1 paise x 3.4 -> 4, plus 1.
        assert by_slug["deep"]["reply_credits"] == 4
        models = {m["slug"]: m for m in menu["vendors"][0]["models"]}
        assert models["model:openai/gpt-5-mini"]["reply_credits"] == 1
        assert models["model:openai/gpt-5"]["reply_credits"] == 4

    async def test_off_the_menu_is_unchanged(self, rule_off):
        from api.services.configuration import chat_presets

        with patch(
            "api.services.configuration.platform_credentials.managed_providers",
            new=AsyncMock(return_value={"llm": ["openai"]}),
        ):
            menu = await chat_presets.menu(object())
        assert "reply_credits" not in menu["presets"][0]
        assert "reply_credits" not in menu["vendors"][0]["models"][0]


async def _rate_card() -> dict:
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=42, selected_organization_id=7
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await http.get("/api/v1/billing/rate-card")
    finally:
        app.dependency_overrides.pop(get_user, None)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
class TestTheRateCard:
    async def test_on_it_is_the_exchange(self, rule_on):
        body = await _rate_card()
        assert body["version"] == "2026-09-25"
        assert body["enabled"] is True
        assert body["paise_per_credit"] == 50
        assert body["included_model_paise_per_credit"] == 14.71
        assert body["premium_model_multiplier"] == 3.4
        figures = {line["key"]: line["credits"] for line in body["lines"]}
        assert figures["knowledge_answer"] == 1
        assert figures["voice_minute"] == 12
        assert figures["whatsapp_marketing"] == 3
        assert figures["premium_model_tokens"] is None

    async def test_off_it_is_todays_figures(self, rule_off):
        body = await _rate_card()
        assert body["enabled"] is False
        figures = {line["key"]: line["credits"] for line in body["lines"]}
        assert figures["knowledge_answer"] == 2
        assert figures["tool_call_premium"] == 3
        assert body["included_model_paise_per_credit"] is None
        assert body["premium_model_multiplier"] is None
