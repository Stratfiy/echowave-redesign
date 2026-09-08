"""What the database tells the decision engine.

`decide` is exhaustively tested against a `History` built by hand, which proves
the policy and nothing about whether the real one is assembled correctly. That
seam is where a guard silently stops guarding: every rule still passes its own
test while the field it depends on arrives as `False` for ever.

A mutation found exactly that here — `charge_started` hardcoded to `False` broke
no test at all, and it is the field standing between a crashed charge and a
second one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import AutoTopupAttemptModel, OrganizationModel
from api.services.billing import auto_topup_runner

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _attempt(session, org_id: int, **kw) -> AutoTopupAttemptModel:
    base = dict(
        organization_id=org_id,
        status=auto_topup_runner.SCHEDULED,
        amount_paise=100_000,
    )
    base.update(kw)
    row = AutoTopupAttemptModel(**base)
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
class TestHistoryReflectsWhatIsActuallyThere:
    async def test_a_charging_attempt_reports_charge_started(
        self, db_session, async_session
    ):
        """The field that stops a crashed charge being presented twice."""
        org = await _org(async_session, "started")
        await _attempt(async_session, org.id, status=auto_topup_runner.CHARGING)

        history = await auto_topup_runner.load_history(
            async_session, organization_id=org.id, now=NOW
        )
        assert history.charge_started is True
        assert history.in_flight is True

    async def test_a_scheduled_attempt_does_not(self, db_session, async_session):
        """Otherwise the first debit is refused as a crashed one and auto
        top-up never charges anything at all."""
        org = await _org(async_session, "scheduled")
        await _attempt(async_session, org.id, notified_at=NOW)

        history = await auto_topup_runner.load_history(
            async_session, organization_id=org.id, now=NOW
        )
        assert history.charge_started is False
        assert history.in_flight is True
        assert history.notified_at is not None

    async def test_an_account_with_no_attempts_is_clean(
        self, db_session, async_session
    ):
        org = await _org(async_session, "clean")
        history = await auto_topup_runner.load_history(
            async_session, organization_id=org.id, now=NOW
        )
        assert history.in_flight is False
        assert history.charge_started is False
        assert history.notified_at is None
        assert history.count_this_month == 0

    async def test_a_finished_attempt_is_not_in_flight(self, db_session, async_session):
        org = await _org(async_session, "done")
        await _attempt(
            async_session,
            org.id,
            status=auto_topup_runner.SUCCEEDED,
            charged_at=NOW - timedelta(days=1),
        )
        history = await auto_topup_runner.load_history(
            async_session, organization_id=org.id, now=NOW
        )
        assert history.in_flight is False
        assert history.charge_started is False

    async def test_this_months_successes_are_counted_and_summed(
        self, db_session, async_session
    ):
        """Both halves of the runaway ceiling read from here, so a wrong count
        or a wrong total is a ceiling that does not hold."""
        org = await _org(async_session, "counted")
        for day in (1, 2):
            await _attempt(
                async_session,
                org.id,
                status=auto_topup_runner.SUCCEEDED,
                charged_at=NOW - timedelta(days=day),
            )
        history = await auto_topup_runner.load_history(
            async_session, organization_id=org.id, now=NOW
        )
        assert history.count_this_month == 2
        assert history.charged_this_month_paise == 200_000

    async def test_last_months_successes_do_not_count_against_this_month(
        self, db_session, async_session
    ):
        """A ceiling that never resets is a feature that switches itself off
        after the first busy month."""
        org = await _org(async_session, "lastmonth")
        await _attempt(
            async_session,
            org.id,
            status=auto_topup_runner.SUCCEEDED,
            charged_at=NOW - timedelta(days=40),
        )
        history = await auto_topup_runner.load_history(
            async_session, organization_id=org.id, now=NOW
        )
        assert history.count_this_month == 0
        assert history.charged_this_month_paise == 0

    async def test_failures_since_the_last_success_are_counted(
        self, db_session, async_session
    ):
        org = await _org(async_session, "failures")
        for _ in range(2):
            await _attempt(async_session, org.id, status=auto_topup_runner.FAILED)
        history = await auto_topup_runner.load_history(
            async_session, organization_id=org.id, now=NOW
        )
        assert history.consecutive_failures == 2

    async def test_a_success_resets_the_failure_run(self, db_session, async_session):
        """Counting every failure ever would pause an account permanently after
        three bad months across a year of working ones."""
        org = await _org(async_session, "reset")
        await _attempt(async_session, org.id, status=auto_topup_runner.FAILED)
        await _attempt(
            async_session,
            org.id,
            status=auto_topup_runner.SUCCEEDED,
            charged_at=NOW,
        )
        history = await auto_topup_runner.load_history(
            async_session, organization_id=org.id, now=NOW
        )
        assert history.consecutive_failures == 0


class TestTheRowIsCommittedBeforeTheBankIsCalled:
    """A durability property, asserted structurally because no unit test can
    observe it: it is about what survives a process dying mid-request.

    A flush here rather than a commit means the `charging` status rolls back
    with the transaction, and the next sweep presents the card again for a debit
    the bank may already have taken.
    """

    def test_execute_commits_the_charging_status(self):
        import inspect

        source = inspect.getsource(auto_topup_runner.execute)
        charging_at = source.index("attempt.status = CHARGING")
        charge_at = source.index("charge_saved_token")
        commit_at = source.index("await session.commit()")

        assert charging_at < commit_at < charge_at, (
            "the charging status must be committed between being set and the "
            "bank being called, or a crash rolls it back and the next sweep "
            "charges again"
        )
