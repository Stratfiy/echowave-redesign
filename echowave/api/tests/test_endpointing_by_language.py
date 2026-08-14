"""Endpointing per language, and which transcriber earned it.

The endpointing work shipped measured but never checked. The medians it
produced were global, which averages a language paying no silence wait with
one paying half a second on every turn — a number describing neither.

The distinction is Flux. Deepgram's Flux emits a turn-ended signal, so a turn
on it waits for nothing; every other transcriber waits out ``stop_secs`` and
then ``user_speech_timeout``, every single turn. Flux does not support Tamil,
Telugu, Kannada, Marathi or Bengali, so those fall back and pay a gap Hindi and
English no longer pay. That gap is Deepgram's; explaining it to a customer is
ours, and this is the figure to explain it with.

Two decisions worth holding:

**The transcriber comes from the call's cost lines, not from configuration.**
Configuration says what the agent was set to; the cost line says what actually
ran. A managed tier repointed mid-window makes those different answers, and the
one that matters is what ran.

**Flux is decided by the model name, not the vendor.** Deepgram's own Nova
waits like everything else, so keying on "deepgram" would report a slow turn as
a fast one.
"""

from datetime import UTC, datetime

import pytest

from api.db.billing_dashboard_client import endpointing_by_language


async def _call(async_session, slug, *, language, stt_model, endpointing_ms, turns=1):
    from api.db.models import (
        CallCostItemModel,
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
        language=language,
        created_at=datetime.now(UTC),
        initial_context={},
        gathered_context={},
    )
    async_session.add(run)
    await async_session.flush()

    if stt_model is not None:
        async_session.add(
            CallCostItemModel(
                workflow_run_id=run.id,
                component="stt",
                provider="deepgram",
                model=stt_model,
                units=1,
                cost_paise=1,
            )
        )

    for index in range(turns):
        async_session.add(
            CallTurnMetricModel(
                workflow_run_id=run.id,
                turn_index=index,
                t_user_stopped_ms=1000,
                t_endpoint_fired_ms=1000 + endpointing_ms,
                created_at=datetime.now(UTC),
            )
        )
    await async_session.flush()
    return run


def _today():
    from api.services.billing.rollup import IST

    return datetime.now(IST).date()


@pytest.mark.asyncio
class TestTheBreakdown:
    async def test_each_language_reports_its_own_median(
        self, db_session, async_session
    ):
        await _call(
            async_session,
            "hi",
            language="hi",
            stt_model="flux-general-multi",
            endpointing_ms=60,
        )
        await _call(
            async_session,
            "ta",
            language="ta",
            stt_model="nova-3-general",
            endpointing_ms=560,
        )

        rows = await endpointing_by_language(
            async_session, start=_today(), end=_today()
        )
        by_language = {r["language"]: r for r in rows}

        assert by_language["hi"]["p50_ms"] == 60
        assert by_language["ta"]["p50_ms"] == 560

    async def test_flux_is_marked_as_not_waiting_for_silence(
        self, db_session, async_session
    ):
        await _call(
            async_session,
            "flux",
            language="en",
            stt_model="flux-general-multi",
            endpointing_ms=50,
        )

        row = next(
            r
            for r in await endpointing_by_language(
                async_session, start=_today(), end=_today()
            )
            if r["stt_model"] == "flux-general-multi"
        )
        assert row["waits_for_silence"] is False

    async def test_deepgrams_own_nova_still_waits(self, db_session, async_session):
        """Keyed on the model, not the vendor. Nova is Deepgram and waits like
        everything else, so treating "deepgram" as fast would report a slow
        turn as a fast one."""
        await _call(
            async_session,
            "nova",
            language="en",
            stt_model="nova-3-general",
            endpointing_ms=520,
        )

        row = next(
            r
            for r in await endpointing_by_language(
                async_session, start=_today(), end=_today()
            )
            if r["stt_model"] == "nova-3-general"
        )
        assert row["waits_for_silence"] is True

    async def test_a_call_with_no_stt_line_is_reported_not_dropped(
        self, db_session, async_session
    ):
        """An outer join, deliberately. Dropping those turns would quietly
        shrink the sample the medians are computed over, and the reader would
        have no way to tell."""
        await _call(
            async_session, "nostt", language="mr", stt_model=None, endpointing_ms=500
        )

        rows = await endpointing_by_language(
            async_session, start=_today(), end=_today()
        )
        row = next(r for r in rows if r["language"] == "mr")
        assert row["stt_model"] == "(unrecorded)"
        assert row["p50_ms"] == 500

    async def test_turns_with_no_release_mark_are_excluded(
        self, db_session, async_session
    ):
        """Not counted as zero. A zero would halve the median of the stage
        that is usually the largest, and read as an improvement."""
        from api.db.models import CallTurnMetricModel

        run = await _call(
            async_session,
            "partial",
            language="kn",
            stt_model="nova-3-general",
            endpointing_ms=600,
        )
        async_session.add(
            CallTurnMetricModel(
                workflow_run_id=run.id,
                turn_index=99,
                t_user_stopped_ms=1000,
                t_endpoint_fired_ms=None,
                created_at=datetime.now(UTC),
            )
        )
        await async_session.flush()

        row = next(
            r
            for r in await endpointing_by_language(
                async_session, start=_today(), end=_today()
            )
            if r["language"] == "kn"
        )
        assert row["turns"] == 1
        assert row["p50_ms"] == 600

    async def test_a_call_with_no_language_is_left_out(self, db_session, async_session):
        """The table is a per-language breakdown. A row labelled None is not a
        language and would sit at the top on volume, explaining nothing."""
        await _call(
            async_session,
            "nolang",
            language=None,
            stt_model="nova-3-general",
            endpointing_ms=400,
        )

        rows = await endpointing_by_language(
            async_session, start=_today(), end=_today()
        )
        assert all(r["language"] is not None for r in rows)

    async def test_the_gap_flux_closes_is_visible_in_one_table(
        self, db_session, async_session
    ):
        """The whole point: a customer asking "why is Telugu slower" gets an
        answer with a number in it."""
        await _call(
            async_session,
            "gap-en",
            language="en",
            stt_model="flux-general-multi",
            endpointing_ms=60,
            turns=3,
        )
        await _call(
            async_session,
            "gap-te",
            language="te",
            stt_model="nova-3-general",
            endpointing_ms=580,
            turns=3,
        )

        rows = await endpointing_by_language(
            async_session, start=_today(), end=_today()
        )
        fast = next(r for r in rows if r["language"] == "en")
        slow = next(r for r in rows if r["language"] == "te")

        assert slow["p50_ms"] - fast["p50_ms"] > 400
        assert fast["waits_for_silence"] is False
        assert slow["waits_for_silence"] is True
