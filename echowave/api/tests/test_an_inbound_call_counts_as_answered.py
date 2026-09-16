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


class TestTheRateCountsOnePopulation:
    """`calls` and `answered` were drawn from different populations, so the
    rate could not be right whatever the rule said.

    `calls` counted every non-test run -- browser calls and text chats
    included. `answered` counted only CARRIER_RUN_MODES. A browser call was
    therefore countable as a call and not countable as answered, so a
    workspace demonstrating over the web widget read "7 calls, 0 answered"
    forever, while each of those runs said `answered: true` on its own
    timeline with thirty-five seconds of talk time. Verified on the
    founder's live account: mode `smallwebrtc`, duration 35s, answered true.
    """

    def _where(self) -> str:
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "db" / "app_interaction_client.py"
        ).read_text()
        body = source.split("async def agent_activity", 1)[1]
        return body.split("# Runs that reached an outside system", 1)[0]

    def test_answered_is_no_longer_gated_on_the_carrier_allow_list(self):
        # The gate existed because the old rule (answered_at alone, which
        # only a carrier writes) made a first day of testing look like
        # nobody picked up. The rule now accepts billable seconds, so the
        # gate had outlived its reason while causing a worse version of the
        # thing it prevented: an exactly-zero answer rate.
        # Asserted by counting rather than by slicing the source: the
        # allow-list belongs to `dialled` and to nothing else in this query,
        # so exactly one mention is the whole invariant.
        where = self._where()
        # The code usage, not the word: the docstring above the query
        # explains the allow-list at length and would confound a plain count.
        use = "WorkflowRunModel.mode.in_(CARRIER_RUN_MODES)"
        assert where.count(use) == 1, where.count(use)
        assert "answered_sql()" in where

    def test_a_text_chat_is_not_counted_as_a_call(self):
        # The other half. Somebody typing in a chat window has not made a
        # call, and `is_voice_run_mode` already said so.
        assert "NON_VOICE_RUN_MODES" in self._where()

    def test_the_carrier_list_still_decides_what_was_dialled(self):
        # Attempts over a carrier are a different, real metric; this change
        # is about the answered rate, not about it.
        where = self._where()
        assert "WorkflowRunModel.mode.in_(CARRIER_RUN_MODES)" in where
