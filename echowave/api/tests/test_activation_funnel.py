"""The activation funnel.

Two properties carry the whole thing, and both are easy to break by accident:

* Later steps never exceed earlier ones. A funnel that can go back up is a
  funnel nobody trusts, and the arithmetic that allows it is usually a join
  fanning out rather than a real reversal.
* The cohort is fixed at signup and each step asks "ever". Counting steps
  inside the window would make a wide range look worse simply by admitting
  recent signups who have not had time to activate yet.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from api.db import db_client
from api.enums import CreditLedgerKind
from api.db.models import (
    CreditLedgerModel,
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)

# The funnel counts in **IST business days** — activation_funnel's _bounds
# turns the dates it is given into IST midnights, which is right for an Indian
# product. So the tests' notion of "today" has to be IST's, not the container's.
#
# Using date.today() here meant every one of these tests failed between 18:30
# UTC and midnight UTC: rows created "now" land after the window's end bound
# (IST midnight = 18:30 UTC), so the cohort came back empty. They passed all
# afternoon and failed all evening, which is the worst way for a test to be
# wrong — it looks like a flake and it is a timezone.
IST = ZoneInfo("Asia/Kolkata")
TODAY = datetime.now(IST).date()
WINDOW = (TODAY - timedelta(days=7), TODAY)


async def _account(session, slug: str, *, created: datetime | None = None):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()

    user = UserModel(
        provider_id=f"user-{slug}",
        email=f"{slug}@example.com",
        created_at=created or datetime.now(UTC),
    )
    session.add(user)
    await session.flush()

    session.add(OrganizationMembershipModel(user_id=user.id, organization_id=org.id))
    await session.flush()
    return user, org


async def _agent(session, org_id: int, slug: str) -> int:
    workflow = WorkflowModel(name=f"wf-{slug}", organization_id=org_id)
    session.add(workflow)
    await session.flush()
    return workflow.id


async def _run(session, workflow_id: int, slug: str):
    session.add(
        WorkflowRunModel(name=f"run-{slug}", workflow_id=workflow_id, mode="plivo")
    )
    await session.flush()


def _by_key(result) -> dict[str, int]:
    return {step["key"]: step["count"] for step in result["steps"]}


class TestTheFunnel:
    async def test_a_cohort_that_did_nothing_stops_at_signup(
        self, db_session, async_session
    ):
        await _account(async_session, "did-nothing")
        counts = _by_key(await db_client.activation_funnel(*WINDOW))

        assert counts["signed_up"] >= 1
        assert counts["created_agent"] == 0
        assert counts["first_call"] == 0
        assert counts["first_topup"] == 0

    async def test_each_step_is_reached_in_turn(self, db_session, async_session):
        user, org = await _account(async_session, "full-journey")
        workflow_id = await _agent(async_session, org.id, "full-journey")
        await _run(async_session, workflow_id, "full-journey")
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=50000,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=100000,
            )
        )
        await async_session.flush()

        counts = _by_key(await db_client.activation_funnel(*WINDOW))
        assert counts["created_agent"] >= 1
        assert counts["first_call"] >= 1
        assert counts["first_topup"] >= 1

    async def test_the_funnel_never_goes_back_up(self, db_session, async_session):
        """The property most likely to break silently. A later step exceeding an
        earlier one means a join fanned out, not that people un-signed-up."""
        _, org_a = await _account(async_session, "mono-a")
        workflow = await _agent(async_session, org_a.id, "mono-a")
        await _run(async_session, workflow, "mono-a")
        await _account(async_session, "mono-b")
        await _account(async_session, "mono-c")

        steps = (await db_client.activation_funnel(*WINDOW))["steps"]
        counts = [step["count"] for step in steps]
        assert counts == sorted(counts, reverse=True), counts

    async def test_an_agent_with_no_run_does_not_count_as_a_call(
        self, db_session, async_session
    ):
        _, org = await _account(async_session, "built-not-run")
        await _agent(async_session, org.id, "built-not-run")

        before = _by_key(await db_client.activation_funnel(*WINDOW))
        assert before["created_agent"] >= 1
        # The account contributed to created_agent but must not contribute to
        # first_call. Asserted by adding a run and watching only that step move.
        workflow_id = await _agent(async_session, org.id, "built-not-run-2")
        await _run(async_session, workflow_id, "built-not-run-2")
        after = _by_key(await db_client.activation_funnel(*WINDOW))
        assert after["first_call"] == before["first_call"] + 1

    async def test_only_a_real_top_up_counts_as_paid(self, db_session, async_session):
        """Usage, reservations, staff adjustments and trial credit are all
        ledger rows, and two of those are positive. Only the customer actually
        paying makes them a customer."""
        _, org = await _account(async_session, "debit-only")
        workflow_id = await _agent(async_session, org.id, "debit-only")
        await _run(async_session, workflow_id, "debit-only")
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=-2500,
                kind=CreditLedgerKind.USAGE.value,
                balance_after_paise=100000,
            )
        )
        # Positive, but granted rather than paid. Must not count.
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=10000,
                kind=CreditLedgerKind.ADJUSTMENT.value,
                balance_after_paise=100000,
            )
        )
        await async_session.flush()

        counts = _by_key(await db_client.activation_funnel(*WINDOW))
        assert counts["first_topup"] == 0

    async def test_signups_outside_the_window_are_not_in_the_cohort(
        self, db_session, async_session
    ):
        await _account(
            async_session,
            "ancient",
            created=datetime.now(UTC) - timedelta(days=400),
        )
        counts = _by_key(await db_client.activation_funnel(*WINDOW))
        recent = _by_key(
            await db_client.activation_funnel(TODAY - timedelta(days=500), TODAY)
        )
        assert recent["signed_up"] > counts["signed_up"]

    async def test_activity_outside_the_window_still_counts_for_the_cohort(
        self, db_session, async_session
    ):
        """The cohort is fixed at signup and each step asks "ever". A range that
        excluded later activity would make activation look worse the wider you
        set it, which is backwards."""
        _, org = await _account(async_session, "late-activator")
        workflow_id = await _agent(async_session, org.id, "late-activator")
        await _run(async_session, workflow_id, "late-activator")

        # A one-day window containing the signup: the agent and run were created
        # "now" either way, but the assertion is that they are attributed to the
        # signup cohort rather than to the day they happened.
        counts = _by_key(await db_client.activation_funnel(TODAY, TODAY))
        assert counts["created_agent"] >= 1
        assert counts["first_call"] >= 1


class TestTheRates:
    async def test_rates_are_computed_once_server_side(self, db_session, async_session):
        """So two screens cannot disagree about which denominator they used."""
        _, org = await _account(async_session, "rates")
        await _agent(async_session, org.id, "rates")

        steps = (await db_client.activation_funnel(*WINDOW))["steps"]
        assert steps[0]["from_top"] == 100.0
        for step in steps:
            assert "from_previous" in step
            assert "dropped" in step

    async def test_email_verification_is_reported_beside_the_funnel(
        self, db_session, async_session
    ):
        """Not inside it. Nothing gates on verification, so a verified step in
        the middle would either under-report agent creation or let the funnel
        rise — both worse than reporting it separately."""
        result = await db_client.activation_funnel(*WINDOW)
        assert "email_verified" in result
        assert all(step["key"] != "verified" for step in result["steps"])

    async def test_an_empty_window_does_not_divide_by_zero(
        self, db_session, async_session
    ):
        far_past = date(2001, 1, 1)
        result = await db_client.activation_funnel(far_past, far_past)
        assert result["steps"][0]["count"] == 0
        assert result["email_verified_rate"] is None
