"""An answered inbound call is counted as answered.

The founder was told "15 calls today, 0 answered" about a day in which
people had plainly been getting through. ``answered_at`` is stamped by the
carrier's status webhook and only an outbound call has one, so every
inbound call read as unanswered in the headline -- while reading as
answered on its own timeline, from the same rows.

The rule had already been fixed twice (a backfill, then the timeline) and
each fix left another copy behind. These pin the shared one.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from api.services.workflow import answered


def run(**kw):
    base = {"answered_at": None, "billable_seconds": None, "ended_at": None}
    return SimpleNamespace(**{**base, **kw})


class TestTheRule:
    def test_the_carrier_stamp_is_enough(self):
        assert answered.was_answered(run(answered_at=datetime.now(UTC))) is True

    def test_billable_seconds_are_enough_without_a_stamp(self):
        # This is the inbound case, and the whole bug: somebody was on the
        # line long enough to be charged for it.
        assert answered.was_answered(run(billable_seconds=42)) is True

    def test_a_call_nobody_took_is_not_answered(self):
        assert answered.was_answered(run(billable_seconds=0)) is False

    def test_an_uncosted_run_falls_back_to_the_carrier_window(self):
        # Ended but not yet costed: it still knows how long it was connected.
        start = datetime.now(UTC)
        assert (
            answered.was_answered(
                run(answered_at=None, ended_at=start + timedelta(seconds=30))
            )
            is False
        )
        assert (
            answered.was_answered(
                run(answered_at=start, ended_at=start + timedelta(seconds=30))
            )
            is True
        )

    def test_nothing_known_is_not_answered(self):
        # Never claim an answer we have no evidence for. A run still in
        # flight has neither stamp nor seconds.
        assert answered.was_answered(run()) is False


class TestTheHeadlineUsesTheSameRule:
    def test_the_activity_query_does_not_judge_on_the_stamp_alone(self):
        """Guards the regression directly: the compiled SQL must consider
        billable seconds, not only ``answered_at``."""
        sql = str(answered.answered_sql().compile())
        assert "answered_at" in sql
        assert "billable_seconds" in sql
