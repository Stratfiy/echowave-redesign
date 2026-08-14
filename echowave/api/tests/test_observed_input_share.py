"""Measuring the blend assumption instead of assuming it.

``LLM_INPUT_SHARE`` folds a vendor's two-sided price — input and output charged
differently — into the single per-1k rate the schema carries. It is set to 0.7
because somebody had to pick a number, and every LLM margin figure on every
screen rests on it.

It never needed to be a guess. ``call_turn_metrics`` has recorded
``prompt_tokens`` and ``completion_tokens`` per turn since latency
instrumentation shipped, so the true share is a division nobody was performing.

Which direction the error runs matters. Output tokens cost several times input
— for gpt-4o, four times — so a share set **too high** assumes cheap tokens we
are not getting, understates what we pay, and overstates every margin computed
from it. The drift is reported signed so that direction is legible rather than
something to work out from first principles at the moment it matters.
"""

from datetime import UTC, datetime

import pytest

from api.db.billing_dashboard_client import observed_input_share
from api.services.billing.default_rates import LLM_INPUT_SHARE


async def _run_with_turns(async_session, slug, turns):
    """A call and its turns. ``turns`` is (prompt, completion, cached)."""
    from api.db.models import (
        CallTurnMetricModel,
        OrganizationModel,
        WorkflowModel,
        WorkflowRunModel,
    )

    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()

    workflow = WorkflowModel(name=f"wf-{slug}", organization_id=org.id)
    async_session.add(workflow)
    await async_session.flush()

    run = WorkflowRunModel(
        name=f"run-{slug}",
        workflow_id=workflow.id,
        mode="twilio",
        created_at=datetime.now(UTC),
        initial_context={},
        gathered_context={},
    )
    async_session.add(run)
    await async_session.flush()

    for index, (prompt, completion, cached) in enumerate(turns):
        async_session.add(
            CallTurnMetricModel(
                workflow_run_id=run.id,
                turn_index=index,
                prompt_tokens=prompt,
                completion_tokens=completion,
                cached_tokens=cached,
            )
        )
    await async_session.flush()
    return org


def _today():
    from api.services.billing.rollup import IST

    return datetime.now(IST).date()


@pytest.mark.asyncio
class TestTheMeasurement:
    async def test_it_divides_input_by_the_total(self, db_session, async_session):
        org = await _run_with_turns(
            async_session, "share", [(700, 300, 0), (1400, 600, 0)]
        )

        result = await observed_input_share(
            async_session, start=_today(), end=_today(), organization_id=org.id
        )

        assert result["observed"] == 0.7
        assert result["prompt_tokens"] == 2100
        assert result["completion_tokens"] == 900

    async def test_the_drift_is_signed_towards_the_expensive_mistake(
        self, db_session, async_session
    ):
        """Positive drift means we assumed more input than there is. Output is
        the dearer side, so that is the direction that understates cost."""
        org = await _run_with_turns(async_session, "drift", [(500, 500, 0)])

        result = await observed_input_share(
            async_session, start=_today(), end=_today(), organization_id=org.id
        )

        assert result["observed"] == 0.5
        assert result["drift"] == pytest.approx(LLM_INPUT_SHARE - 0.5)
        assert result["drift"] > 0

    async def test_nothing_recorded_reports_none_rather_than_zero(
        self, db_session, async_session
    ):
        """A provider that never reported usage is silent, not all-output. A
        zero would read as "we assumed badly" when it means "we measured
        nothing", and somebody would move the constant on the strength of it.
        """
        result = await observed_input_share(
            async_session, start=_today(), end=_today(), organization_id=-1
        )

        assert result["observed"] is None
        assert result["drift"] is None
        assert result["turns"] == 0

    async def test_turns_that_reported_nothing_are_excluded_not_counted_as_zero(
        self, db_session, async_session
    ):
        """The same argument per row. One silent turn among ten must not drag
        the measured share towards zero."""
        org = await _run_with_turns(
            async_session, "partial", [(700, 300, 0), (None, None, None)]
        )

        result = await observed_input_share(
            async_session, start=_today(), end=_today(), organization_id=org.id
        )

        assert result["turns"] == 1
        assert result["observed"] == 0.7

    async def test_the_cache_hit_rate_comes_back_too(self, db_session, async_session):
        """The lever on all of it. A cached input token costs a fraction of a
        fresh one, so a high input share is cheaper than it looks when the hit
        rate is high — and the two figures are only useful together."""
        org = await _run_with_turns(async_session, "cache", [(1000, 200, 800)])

        result = await observed_input_share(
            async_session, start=_today(), end=_today(), organization_id=org.id
        )

        assert result["cached_tokens"] == 800
        assert result["cached_share"] == 0.8

    async def test_it_reports_what_it_assumed_so_the_two_are_comparable(
        self, db_session, async_session
    ):
        """The screen shows both. Reporting only the observed figure would
        leave the reader to remember what it is being compared against."""
        result = await observed_input_share(
            async_session, start=_today(), end=_today(), organization_id=-1
        )

        assert result["assumed"] == LLM_INPUT_SHARE

    async def test_one_organization_cannot_see_anothers_tokens(
        self, db_session, async_session
    ):
        """Org-scoped like every other figure on this screen."""
        mine = await _run_with_turns(async_session, "mine", [(900, 100, 0)])
        await _run_with_turns(async_session, "theirs", [(100, 900, 0)])

        result = await observed_input_share(
            async_session, start=_today(), end=_today(), organization_id=mine.id
        )

        assert result["observed"] == 0.9
