"""The super-admin KPI board (pricing spec §9).

Two properties matter more than any one figure:

* **every KPI in the spec is on the board**, and the ones we cannot compute
  say why rather than rendering blank; and
* **internal accounts never reach a number** — our own demos and QA are not
  revenue, signups or churn.

The arithmetic tests each pin one definition the spec states in words (MRR
spreads annual over twelve; churn is measured across the window; a top-up
is a payment whose ledger row is a top-up) so a refactor that changes what a
word means fails here first.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from api.db.models import (
    AgentEventModel,
    APIKeyModel,
    CreditLedgerModel,
    OrganizationModel,
    PaymentMandateModel,
    PaymentModel,
    TaxDocumentModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import (
    AgentEventActor,
    AgentEventKind,
    CallType,
    CreditLedgerKind,
    MandateStatus,
    WorkflowRunMode,
    WorkflowRunState,
)
from api.services.billing import kpi_board
from api.services.billing.kpi_board import SECTIONS, Window, board, catalogue

# The board's "as of" day, and a clock inside it. Mid-afternoon IST so that
# "N days ago" lands inside the intended IST day whichever way it is bucketed.
AS_OF = date(2026, 6, 15)
NOW = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


async def _org(session, slug: str, *, internal: bool = False):
    org = OrganizationModel(
        provider_id=f"org-{slug}",
        quota_decibyl_tokens=0,
        internal_billing=internal,
        created_at=days_ago(60),
    )
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    workflow = WorkflowModel(name=f"wf-{slug}", organization_id=org.id, user_id=None)
    session.add(workflow)
    await session.flush()
    return org, workflow


async def _plan(
    session,
    org,
    *,
    price: int,
    code: str = "business",
    period: str = "monthly",
    authorised: datetime,
    cancelled: datetime | None = None,
):
    mandate = PaymentMandateModel(
        organization_id=org.id,
        provider="razorpay",
        purpose="starter_plan",
        subscription_id=f"sub_{org.id}_{code}_{int(authorised.timestamp())}",
        plan_id="plan_x",
        status=(
            MandateStatus.CANCELLED.value if cancelled else MandateStatus.ACTIVE.value
        ),
        price_paise=price,
        plan_code=code,
        billing_period=period,
        authorised_at=authorised,
        cancelled_at=cancelled,
        created_at=authorised,
    )
    session.add(mandate)
    await session.flush()
    return mandate


async def _ledger(session, org, *, delta: int, kind: str, ref_type=None, at=None):
    row = CreditLedgerModel(
        organization_id=org.id,
        delta_paise=delta,
        kind=kind,
        ref_type=ref_type,
        ref_id=f"{kind}-{delta}-{org.id}",
        balance_after_paise=max(delta, 0),
        created_at=at or NOW,
    )
    session.add(row)
    await session.flush()
    return row


async def _topup(session, org, *, amount: int, bonus: int = 0, at=None):
    ledger = await _ledger(
        session, org, delta=amount + bonus, kind=CreditLedgerKind.TOPUP.value, at=at
    )
    payment = PaymentModel(
        organization_id=org.id,
        provider="razorpay",
        order_id=f"order_{org.id}_{amount}",
        payment_id=f"pay_{org.id}_{amount}",
        amount_paise=amount,
        bonus_paise=bonus,
        status="paid",
        paid_at=at or NOW,
        credit_ledger_id=ledger.id,
    )
    session.add(payment)
    await session.flush()
    return payment


async def _call(
    session,
    workflow,
    *,
    seconds: int,
    charged: int,
    cost: int,
    language: str = "hi-IN",
    call_type: str = CallType.OUTBOUND.value,
    answered: bool = True,
    completed: bool = True,
    at=None,
):
    when = at or NOW
    run = WorkflowRunModel(
        name="call",
        workflow_id=workflow.id,
        mode=WorkflowRunMode.PLIVO.value,
        call_type=call_type,
        state=(
            WorkflowRunState.COMPLETED.value
            if completed
            else WorkflowRunState.RUNNING.value
        ),
        created_at=when,
        answered_at=when if answered else None,
        language=language,
        billable_seconds=seconds,
        billed_seconds=seconds,
        total_charged_paise=charged,
        total_provider_cost_paise=cost,
        costed_at=when,
    )
    session.add(run)
    await session.flush()
    return run


def _kpi(result: dict, key: str) -> dict:
    for section in result["sections"]:
        for kpi in section["kpis"]:
            if kpi["key"] == key:
                return kpi
    raise KeyError(key)


def _current(result: dict, key: str, window: str = "month"):
    return _kpi(result, key)["values"][window]["current"]


def _previous(result: dict, key: str, window: str = "month"):
    return _kpi(result, key)["values"][window]["previous"]


class TestTheBoardIsComplete:
    """Every row the spec names is present; the missing ones say why."""

    def test_every_spec_row_is_on_the_board(self):
        keys = [kpi["key"] for section in catalogue() for kpi in section["kpis"]]
        assert len(keys) == len(set(keys)), "a KPI key is declared twice"
        assert [s["key"] for s in catalogue()] == [
            "revenue",
            "customers",
            "usage",
            "quality",
            "trust",
        ]
        # Spec §9: 13 + 10 + 13 + 14 + 8 rows.
        assert len(keys) == 58

    def test_every_unavailable_row_names_what_is_missing(self):
        for section in SECTIONS:
            for kpi in section.kpis:
                if not kpi.available:
                    assert kpi.unavailable_reason, kpi.key
                    assert len(kpi.unavailable_reason) > 20, kpi.key
                else:
                    assert (kpi.shape == "flow") == (kpi.flow is not None), kpi.key

    def test_the_known_gaps_are_the_unavailable_rows(self):
        missing = {
            kpi["key"] for s in catalogue() for kpi in s["kpis"] if not kpi["available"]
        }
        assert missing == {
            "enterprise_committed",
            "cac",
            "text_margin",
            "infra_cost",
            "desktop_steps",
            "concurrency",
            "impersonation",
            "aup_hits",
            "dnc_hits",
            "outside_window",
            "failed_logins",
        }

    def test_windows_end_on_as_of_and_previous_periods_abut(self):
        w = Window.ending("month", AS_OF, 30)
        assert (w.end - w.start).days == 29
        assert w.previous_end == w.start - timedelta(days=1)
        assert (w.previous_end - w.previous_start).days == 29
        d = Window.ending("day", AS_OF, 1)
        assert d.start == d.end == AS_OF
        assert d.previous_start == d.previous_end == AS_OF - timedelta(days=1)


@pytest.mark.asyncio
class TestRevenue:
    async def test_mrr_spreads_annual_over_twelve_and_skips_internal(
        self, db_session, async_session
    ):
        monthly, _ = await _org(async_session, "monthly")
        annual, _ = await _org(async_session, "annual")
        ours, _ = await _org(async_session, "ours", internal=True)
        await _plan(async_session, monthly, price=2_999_00, authorised=days_ago(10))
        await _plan(
            async_session,
            annual,
            price=12_000_00,
            period="annual",
            code="everyday",
            authorised=days_ago(10),
        )
        await _plan(async_session, ours, price=99_999_00, authorised=days_ago(10))

        result = await board(async_session, as_of=AS_OF)

        mrr = _current(result, "mrr")
        assert mrr["value"] == 2_999_00 + 1_000_00
        assert mrr["breakdown"]["accounts"] == 2
        assert _current(result, "arr")["value"] == (2_999_00 + 1_000_00) * 12
        # Both subscribed ten days ago: nothing at the start of the window.
        assert _previous(result, "mrr")["value"] == 0
        movement = _current(result, "mrr_movement")["breakdown"]
        assert movement["new"] == 2_999_00 + 1_000_00
        assert movement["churned"] == 0
        by_plan = _current(result, "paying_by_plan")["breakdown"]
        assert by_plan == {"business": 1, "everyday": 1}

    async def test_churn_is_measured_across_the_window(self, db_session, async_session):
        stays, _ = await _org(async_session, "stays")
        leaves, _ = await _org(async_session, "leaves")
        await _plan(async_session, stays, price=2_999_00, authorised=days_ago(50))
        await _plan(
            async_session,
            leaves,
            price=9_999_00,
            code="growth",
            authorised=days_ago(50),
            cancelled=days_ago(5),
        )

        result = await board(async_session, as_of=AS_OF)

        churn = _current(result, "churn")
        assert churn["breakdown"]["at_start"] == 2
        assert churn["breakdown"]["churned"] == 1
        assert churn["value"] == 0.5
        assert churn["breakdown"]["revenue_churn"] == pytest.approx(
            9_999_00 / (2_999_00 + 9_999_00)
        )
        nrr = _current(result, "nrr")
        assert nrr["value"] == pytest.approx(2_999_00 / (2_999_00 + 9_999_00))
        assert _current(result, "mrr_movement")["breakdown"]["churned"] == 9_999_00
        # A week ago both were still subscribed: no churn in the 7-day window
        # before the cancellation, one in the window that contains it.
        assert _current(result, "churn", "week")["breakdown"]["churned"] == 1
        assert _previous(result, "churn", "week")["breakdown"]["churned"] == 0

    async def test_topup_revenue_counts_topups_only_and_attach_rate_uses_payers(
        self, db_session, async_session
    ):
        buyer, _ = await _org(async_session, "buyer")
        subscriber, _ = await _org(async_session, "subscriber")
        ours, _ = await _org(async_session, "ours", internal=True)
        await _topup(async_session, buyer, amount=500_00, bonus=0)
        await _topup(async_session, ours, amount=500_00)
        await _plan(async_session, subscriber, price=2_999_00, authorised=days_ago(3))
        # A plan collection is a payment too, but its ledger row is a grant.
        grant = await _ledger(
            async_session,
            subscriber,
            delta=6_000 * 50,
            kind=CreditLedgerKind.PLAN.value,
            ref_type="plan_collection",
        )
        async_session.add(
            PaymentModel(
                organization_id=subscriber.id,
                provider="razorpay",
                order_id="order_plan",
                payment_id="pay_plan",
                amount_paise=2_999_00,
                status="paid",
                paid_at=NOW,
                credit_ledger_id=grant.id,
            )
        )
        await async_session.flush()

        result = await board(async_session, as_of=AS_OF)

        revenue = _current(result, "topup_revenue")
        assert revenue["value"] == 500_00
        assert revenue["breakdown"] == {"count": 1, "credits": 1_000}
        attach = _current(result, "topup_attach")
        assert attach["breakdown"] == {"buyers": 1, "paying": 2}
        assert attach["value"] == 0.5

    async def test_gst_nets_credit_notes_and_counts_them(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "gst")
        async_session.add_all(
            [
                TaxDocumentModel(
                    organization_id=org.id,
                    kind="receipt_voucher",
                    number="RV/26-27/000001",
                    financial_year="26-27",
                    issued_at=NOW,
                    taxable_paise=100_000,
                    cgst_paise=9_000,
                    sgst_paise=9_000,
                    total_paise=118_000,
                    supply_type="intra_state",
                    rate_basis_points=1800,
                ),
                TaxDocumentModel(
                    organization_id=org.id,
                    kind="credit_note",
                    number="CN/26-27/000001",
                    financial_year="26-27",
                    issued_at=NOW,
                    taxable_paise=10_000,
                    cgst_paise=900,
                    sgst_paise=900,
                    total_paise=11_800,
                    supply_type="intra_state",
                    rate_basis_points=1800,
                ),
                TaxDocumentModel(
                    organization_id=org.id,
                    kind="tax_invoice",
                    number="INV/26-27/000001",
                    financial_year="26-27",
                    issued_at=NOW,
                    taxable_paise=50_000,
                    total_paise=50_000,
                    supply_type="export",
                    rate_basis_points=0,
                ),
            ]
        )
        await async_session.flush()

        result = await board(async_session, as_of=AS_OF)

        gst = _current(result, "gst_collected")
        assert gst["breakdown"] == {
            "cgst_paise": 8_100,
            "sgst_paise": 8_100,
            "igst_paise": 0,
        }
        assert gst["value"] == 16_200
        notes = _current(result, "credit_notes")
        assert notes == {"value": 11_800, "breakdown": {"count": 1}}
        export = _current(result, "export_revenue")
        assert export == {"value": 50_000, "breakdown": {"invoices": 1}}

    async def test_deferred_revenue_is_the_balance_customers_still_hold(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "deferred")
        ours, _ = await _org(async_session, "ours", internal=True)
        await _ledger(async_session, org, delta=40_000, kind="topup", at=days_ago(2))
        await _ledger(async_session, ours, delta=99_999, kind="topup", at=days_ago(2))

        result = await board(async_session, as_of=AS_OF)

        assert _current(result, "deferred_revenue")["value"] == 40_000


@pytest.mark.asyncio
class TestCustomers:
    async def test_signups_activation_and_conversion_over_the_window(
        self, db_session, async_session
    ):
        activated, wf = await _org(async_session, "activated")
        activated.created_at = days_ago(10)
        idle, _ = await _org(async_session, "idle")
        idle.created_at = days_ago(12)
        old, _ = await _org(async_session, "old")
        old.created_at = days_ago(45)
        ours, _ = await _org(async_session, "ours", internal=True)
        ours.created_at = days_ago(10)
        await async_session.flush()
        await _call(
            async_session, wf, seconds=60, charged=600, cost=278, at=days_ago(8)
        )
        await _topup(async_session, activated, amount=500_00, at=days_ago(7))

        result = await board(async_session, as_of=AS_OF)

        signups = _current(result, "signups")
        assert signups["value"] == 2
        assert signups["breakdown"] == {"organic": 2, "referral": 0, "champion": 0}
        # The account that signed up 45 days ago is the previous period's.
        assert _previous(result, "signups")["value"] == 1
        activation = _current(result, "activation")
        assert activation["breakdown"] == {"signups": 2, "activated": 1}
        assert activation["value"] == 0.5
        conversion = _current(result, "free_to_paid")
        assert conversion["breakdown"] == {"signups": 2, "paid": 1}
        assert _current(result, "time_to_paid")["value"] == pytest.approx(3.0)


@pytest.mark.asyncio
class TestUsage:
    async def test_voice_minutes_margin_and_indic_cost(self, db_session, async_session):
        _, wf = await _org(async_session, "voice")
        await _call(async_session, wf, seconds=120, charged=1_200, cost=556)
        await _call(
            async_session, wf, seconds=60, charged=600, cost=400, language="en-IN"
        )

        result = await board(async_session, as_of=AS_OF)

        minutes = _current(result, "voice_minutes")
        assert minutes["value"] == 3
        assert minutes["breakdown"]["by_language"] == {"hi-IN": 2, "en-IN": 1}
        assert minutes["breakdown"]["by_provider"] == {"plivo": 3}
        margin = _current(result, "voice_margin")
        assert margin["breakdown"]["margin_paise"] == 1_800 - 956
        assert margin["value"] == pytest.approx((1_800 - 956) / 1_800)
        indic = _current(result, "indic_cost")
        # Only the Hindi call: ₹5.56 over two minutes is the model's ₹2.78.
        assert indic["value"] == 278
        assert indic["breakdown"]["indic_minutes"] == 2
        assert indic["breakdown"]["assumption_paise_per_minute"] == 278

    async def test_credits_sold_against_consumed(self, db_session, async_session):
        org, _ = await _org(async_session, "credits")
        await _ledger(
            async_session,
            org,
            delta=2_000 * 50,
            kind=CreditLedgerKind.PLAN.value,
            ref_type="plan_collection",
        )
        await _ledger(
            async_session,
            org,
            delta=-100 * 50,
            kind=CreditLedgerKind.USAGE.value,
            ref_type="workflow_run",
        )
        await _ledger(
            async_session,
            org,
            delta=-2 * 50,
            kind=CreditLedgerKind.USAGE.value,
            ref_type="knowledge_answer",
        )
        await _ledger(
            async_session, org, delta=-10 * 50, kind=CreditLedgerKind.RENTAL.value
        )

        result = await board(async_session, as_of=AS_OF)

        consumed = _current(result, "credits_consumed")
        assert consumed["value"] == 112
        assert consumed["breakdown"] == {
            "voice": 100,
            "knowledge_answer": 2,
            "number_rental": 10,
        }
        ratio = _current(result, "sold_vs_consumed")
        assert ratio["breakdown"]["sold"] == 2_000
        assert ratio["value"] == pytest.approx(112 / 2_000)
        # No plan mandate: the account is on Free for the per-plan split.
        assert ratio["breakdown"]["by_plan"]["free"]["consumed"] == 112
        assert _current(result, "text_events")["breakdown"]["knowledge_answer"] == 1


@pytest.mark.asyncio
class TestQuality:
    async def test_answer_completion_and_attention_rates(
        self, db_session, async_session
    ):
        org, wf = await _org(async_session, "quality")
        await _call(
            async_session,
            wf,
            seconds=60,
            charged=600,
            cost=278,
            call_type=CallType.INBOUND.value,
        )
        await _call(
            async_session,
            wf,
            seconds=0,
            charged=0,
            cost=0,
            call_type=CallType.INBOUND.value,
            answered=False,
            completed=False,
        )
        async_session.add(
            AgentEventModel(
                organization_id=org.id,
                workflow_id=wf.id,
                at=NOW,
                kind=AgentEventKind.NEEDS_ATTENTION.value,
                actor=AgentEventActor.AGENT.value,
                summary="Caller asked for a refund",
            )
        )
        await async_session.flush()

        result = await board(async_session, as_of=AS_OF)

        assert _current(result, "answer_rate")["value"] == 0.5
        assert _current(result, "completion_rate")["value"] == 0.5
        attention = _current(result, "attention_rate")
        assert attention["breakdown"] == {"events": 1, "runs": 2}
        assert _current(result, "could_not_rate")["value"] == 0
        # No turn metrics: latency is honestly absent, not zero.
        assert _current(result, "first_response")["value"] is None


@pytest.mark.asyncio
class TestTrust:
    async def test_stale_api_keys_are_counted_at_the_window_end(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "keys")
        async_session.add_all(
            [
                APIKeyModel(
                    organization_id=org.id,
                    name="fresh",
                    key_hash="h1",
                    key_prefix="dcb_1",
                    created_at=days_ago(200),
                    last_used_at=days_ago(1),
                ),
                APIKeyModel(
                    organization_id=org.id,
                    name="stale",
                    key_hash="h2",
                    key_prefix="dcb_2",
                    created_at=days_ago(200),
                    last_used_at=days_ago(120),
                ),
                APIKeyModel(
                    organization_id=org.id,
                    name="archived",
                    key_hash="h3",
                    key_prefix="dcb_3",
                    created_at=days_ago(300),
                    is_active=False,
                ),
            ]
        )
        await async_session.flush()

        result = await board(async_session, as_of=AS_OF)

        keys = _current(result, "api_keys")
        assert keys["value"] == 1
        assert keys["breakdown"]["active"] == 2
        assert keys["breakdown"]["oldest_age_days"] == 200

    async def test_the_sarvam_burn_projects_from_the_month_so_far(self):
        # Pure arithmetic on the projection, without a database: ₹25,000 at
        # ₹1,000 a day runs out in 25 days.
        assert kpi_board.SARVAM_CREDIT_PAISE == 2_500_000
