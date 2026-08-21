"""Cohort retention: of each signup-month cohort of organizations, who is
still placing calls N months later.

The property that matters most here, and the one most likely to be silently
wrong: a month that has not happened yet must report ``null``, not 0%. A
cohort formed this month, asked about month 3, has not had the chance to
churn or not — reporting 0% would say it already had, and would say so on
every cohort until enough months pass to disprove it, which is exactly the
kind of wrong number nobody double-checks.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from api.db import db_client
from api.db.models import OrganizationModel, WorkflowModel, WorkflowRunModel

IST = ZoneInfo("Asia/Kolkata")

# Mid-month and at noon IST, so nothing here is sensitive to the UTC/IST day
# or month boundary the way a midnight timestamp would be.
NOW = datetime(2026, 6, 15, 12, tzinfo=IST).astimezone(UTC)


def _at(year: int, month: int) -> datetime:
    return datetime(year, month, 15, 12, tzinfo=IST).astimezone(UTC)


async def _org(session, slug: str, *, year: int, month: int) -> OrganizationModel:
    org = OrganizationModel(
        provider_id=f"org-{slug}",
        quota_decibyl_tokens=0,
        created_at=_at(year, month),
    )
    session.add(org)
    await session.flush()
    return org


async def _call(session, org_id: int, slug: str, *, year: int, month: int) -> None:
    workflow = WorkflowModel(name=f"wf-{slug}", organization_id=org_id)
    session.add(workflow)
    await session.flush()
    session.add(
        WorkflowRunModel(
            name=f"run-{slug}",
            workflow_id=workflow.id,
            mode="plivo",
            created_at=_at(year, month),
        )
    )
    await session.flush()


class TestRetentionMatrix:
    async def test_an_org_that_never_calls_reports_zero_in_its_signup_month(
        self, db_session, async_session
    ):
        await _org(async_session, "silent", year=2026, month=6)

        result = await db_client.retention_matrix(
            cohort_months=1, retention_months=1, now=NOW
        )

        cohort = result["cohorts"][0]
        assert cohort["cohort_month"] == "2026-06-01"
        assert cohort["size"] == 1
        assert cohort["retention"][0] == 0.0

    async def test_an_org_active_in_its_signup_month_counts_at_offset_zero(
        self, db_session, async_session
    ):
        org = await _org(async_session, "active-now", year=2026, month=6)
        await _call(async_session, org.id, "active-now", year=2026, month=6)

        result = await db_client.retention_matrix(
            cohort_months=1, retention_months=1, now=NOW
        )

        assert result["cohorts"][0]["retention"][0] == 1.0

    async def test_activity_two_months_later_lands_at_offset_two(
        self, db_session, async_session
    ):
        org = await _org(async_session, "later", year=2026, month=4)
        await _call(async_session, org.id, "later", year=2026, month=6)

        result = await db_client.retention_matrix(
            cohort_months=3, retention_months=3, now=NOW
        )

        cohort = next(c for c in result["cohorts"] if c["cohort_month"] == "2026-04-01")
        assert cohort["retention"][0] == 0.0
        assert cohort["retention"][1] == 0.0
        assert cohort["retention"][2] == 1.0

    async def test_a_month_that_has_not_happened_yet_is_null_not_zero(
        self, db_session, async_session
    ):
        await _org(async_session, "fresh", year=2026, month=6)

        result = await db_client.retention_matrix(
            cohort_months=1, retention_months=3, now=NOW
        )

        retention = result["cohorts"][0]["retention"]
        assert retention[0] == 0.0
        assert retention[1] is None
        assert retention[2] is None

    async def test_cohorts_do_not_leak_into_each_other(self, db_session, async_session):
        org_a = await _org(async_session, "cohort-a", year=2026, month=5)
        await _org(async_session, "cohort-b", year=2026, month=6)
        await _call(async_session, org_a.id, "cohort-a", year=2026, month=6)

        result = await db_client.retention_matrix(
            cohort_months=2, retention_months=2, now=NOW
        )

        by_month = {c["cohort_month"]: c for c in result["cohorts"]}
        assert by_month["2026-05-01"]["retention"][1] == 1.0
        assert by_month["2026-06-01"]["retention"][0] == 0.0

    async def test_an_empty_deployment_reports_zero_size_cohorts_not_an_error(
        self, db_session, async_session
    ):
        result = await db_client.retention_matrix(
            cohort_months=2, retention_months=2, now=NOW
        )

        assert all(c["size"] == 0 for c in result["cohorts"])
        assert all(v is None for c in result["cohorts"] for v in c["retention"])
