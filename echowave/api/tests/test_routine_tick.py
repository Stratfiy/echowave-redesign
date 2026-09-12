"""The minute tick: what it starts, and what it declines to write.

The tests that matter here are the negative ones. A tick that records every
decision would write 1,440 rows a day per routine and produce a timeline
nobody can read -- a worse outcome than the silence it was built to fix.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.enums import AgentEventKind
from api.tasks import routines as tick

IST_HOURS = {
    "enabled": True,
    "slots": [
        {"day_of_week": day, "start_time": "09:30", "end_time": "18:00"}
        for day in range(0, 5)
    ],
}


def routine(**kwargs):
    base = dict(
        id=1,
        organization_id=7,
        workflow_id=42,
        name="Morning numbers",
        instruction="Summarise yesterday.",
        cadence="daily",
        anchor="opening",
        at_minute=0,
        offset_minutes=0,
        weekday=0,
        needs_apps=[],
        is_active=True,
        tested_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_fired_at=None,
        last_skipped_reason=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class Harness:
    """Everything the tick touches, captured rather than executed."""

    def __init__(self, rows, *, failing=frozenset()):
        self.rows = rows
        self.failing = set(failing)
        self.fired: list[tuple[int, datetime]] = []
        self.skipped: list[tuple[int, str]] = []
        self.events: list[dict] = []
        self.enqueued: list[tuple[str, int]] = []

    async def _record(self, **kwargs):
        self.events.append(kwargs)

    def ctx(self):
        redis = SimpleNamespace(
            enqueue_job=AsyncMock(
                side_effect=lambda name, rid: self.enqueued.append((name, rid))
            )
        )
        return {"redis": redis}

    def patches(self, *, timezone="Asia/Kolkata", hours=IST_HOURS):
        prefs = SimpleNamespace(timezone=timezone, business_hours=hours)
        return (
            patch.object(
                db_client, "armed_routines", AsyncMock(return_value=self.rows)
            ),
            patch.object(
                db_client,
                "apps_last_failing",
                AsyncMock(return_value=self.failing),
            ),
            patch.object(
                db_client,
                "mark_routine_fired",
                AsyncMock(
                    side_effect=lambda rid, *, slot: self.fired.append((rid, slot))
                ),
            ),
            patch.object(
                db_client,
                "mark_routine_skipped",
                AsyncMock(
                    side_effect=lambda rid, *, reason: self.skipped.append(
                        (rid, reason)
                    )
                ),
            ),
            patch.object(
                tick, "get_organization_preferences", AsyncMock(return_value=prefs)
            ),
            patch.object(
                tick.agent_timeline, "record", AsyncMock(side_effect=self._record)
            ),
        )

    async def run(self, now, **kwargs):
        import contextlib

        with contextlib.ExitStack() as stack:
            for p in self.patches(**kwargs):
                stack.enter_context(p)
            with patch.object(tick, "datetime") as clock:
                clock.now.return_value = now
                await tick.fire_due_routines(self.ctx())


# 04:00 UTC is 09:30 IST: the opening slot on a Monday.
DUE = datetime(2026, 9, 14, 4, 0, tzinfo=UTC)


class TestFiring:
    async def test_a_due_routine_is_stamped_enqueued_and_recorded(self):
        h = Harness([routine()])
        await h.run(DUE)
        assert len(h.fired) == 1
        assert h.enqueued == [("run_agent_routine", 1)]
        assert [e["kind"] for e in h.events] == [AgentEventKind.ROUTINE_FIRED.value]

    async def test_the_slot_is_stamped_not_the_moment(self):
        # Two minutes late. Stamping now() would make the 09:30 slot look
        # like a 09:32 one, and the next tick would see a different slot.
        h = Harness([routine()])
        await h.run(DUE + timedelta(minutes=2))
        assert h.fired[0][1].hour == 9
        assert h.fired[0][1].minute == 30

    async def test_it_is_stamped_before_the_job_is_enqueued(self):
        # A crash between the two must leave a run that did not happen rather
        # than one that happens twice.
        order: list[str] = []
        h = Harness([routine()])
        h.fired = []

        import contextlib

        with contextlib.ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            stack.enter_context(
                patch.object(
                    db_client,
                    "mark_routine_fired",
                    AsyncMock(side_effect=lambda rid, *, slot: order.append("stamp")),
                )
            )
            redis = SimpleNamespace(
                enqueue_job=AsyncMock(side_effect=lambda *a: order.append("enqueue"))
            )
            with patch.object(tick, "datetime") as clock:
                clock.now.return_value = DUE
                await tick.fire_due_routines({"redis": redis})

        assert order == ["stamp", "enqueue"]

    async def test_the_bots_tenant_is_carried_into_the_event(self):
        h = Harness([routine(organization_id=99)])
        await h.run(DUE)
        assert h.events[0]["organization_id"] == 99
        assert h.events[0]["workflow_id"] == 42


class TestOrdinarySkipsWriteNothing:
    """1,440 rows a day per routine is a worse timeline than no timeline."""

    @pytest.mark.parametrize(
        "now,label",
        [
            (datetime(2026, 9, 14, 3, 0, tzinfo=UTC), "not yet due"),
            (datetime(2026, 9, 13, 5, 0, tzinfo=UTC), "a closed Sunday"),
        ],
    )
    async def test_nothing_is_written(self, now, label):
        h = Harness([routine()])
        await h.run(now)
        assert h.fired == [], label
        assert h.skipped == [], label
        assert h.events == [], label
        assert h.enqueued == [], label

    async def test_an_already_run_slot_writes_nothing(self):
        h = Harness([routine(last_fired_at=datetime(2026, 9, 14, 4, 0, tzinfo=UTC))])
        await h.run(DUE + timedelta(minutes=5))
        assert h.events == []
        assert h.enqueued == []


class TestSkipsWorthKnowingAbout:
    async def test_a_broken_connector_is_recorded_once(self):
        h = Harness([routine(needs_apps=["shopify"])], failing={"shopify"})
        await h.run(DUE)
        assert h.enqueued == []
        assert h.skipped == [(1, "connector_broken")]
        assert [e["kind"] for e in h.events] == [AgentEventKind.ROUTINE_SKIPPED.value]
        assert "shopify" in h.events[0]["summary"]

    async def test_it_is_not_recorded_again_on_the_next_tick(self):
        # The dedupe. "Your Shopify is down" is one line, not a thousand.
        h = Harness(
            [routine(needs_apps=["shopify"], last_skipped_reason="connector_broken")],
            failing={"shopify"},
        )
        await h.run(DUE)
        assert h.skipped == []
        assert h.events == []

    async def test_a_different_reason_is_recorded_even_after_a_skip(self):
        h = Harness(
            [routine(needs_apps=["shopify"], last_skipped_reason="missed")],
            failing={"shopify"},
        )
        await h.run(DUE)
        assert h.skipped == [(1, "connector_broken")]

    async def test_a_missed_run_is_recorded(self):
        h = Harness([routine()])
        await h.run(datetime(2026, 9, 14, 9, 0, tzinfo=UTC))  # 14:30 IST
        assert h.enqueued == []
        assert h.skipped == [(1, "missed")]

    async def test_an_armed_but_untested_routine_is_recorded_not_dropped(self):
        # Filtered in the runtime rather than the query, precisely so this is
        # said rather than vanishing from the tick.
        h = Harness([routine(tested_at=None)])
        await h.run(DUE)
        assert h.enqueued == []
        assert h.skipped == [(1, "never_tested")]


class TestOneRoutineNeverEndsTheTick:
    async def test_a_broken_routine_does_not_stop_the_others(self):
        # Every other business's Desk would silently not run this minute.
        h = Harness([routine(id=1, cadence="nonsense"), routine(id=2)])
        await h.run(DUE)
        assert h.enqueued == [("run_agent_routine", 2)]

    async def test_an_empty_list_does_no_work_at_all(self):
        h = Harness([])
        await h.run(DUE)
        assert h.events == []


class TestPerOrganisationWorkIsDoneOnce:
    async def test_two_desks_in_one_clinic_read_preferences_once(self):
        h = Harness([routine(id=1), routine(id=2)])
        prefs = SimpleNamespace(timezone="Asia/Kolkata", business_hours=IST_HOURS)
        reader = AsyncMock(return_value=prefs)

        import contextlib

        with contextlib.ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            stack.enter_context(
                patch.object(tick, "get_organization_preferences", reader)
            )
            with patch.object(tick, "datetime") as clock:
                clock.now.return_value = DUE
                await tick.fire_due_routines(h.ctx())

        assert reader.await_count == 1

    async def test_failing_apps_are_not_queried_when_no_routine_needs_one(self):
        # A query every minute for every tenant, to answer a question no
        # routine asked.
        h = Harness([routine(needs_apps=[])])
        checker = AsyncMock(return_value=set())

        import contextlib

        with contextlib.ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            stack.enter_context(patch.object(db_client, "apps_last_failing", checker))
            with patch.object(tick, "datetime") as clock:
                clock.now.return_value = DUE
                await tick.fire_due_routines(h.ctx())

        assert checker.await_count == 0
