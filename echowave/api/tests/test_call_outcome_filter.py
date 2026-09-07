"""Filtering the calls list by what the calls achieved.

A call carries several outcome labels, so "matches these two" has two honest
readings and the filter has to say which. Everything here is checked by
compiling the SQL rather than running it: the risk in this code is which
operator gets emitted against a JSON column, and that is visible in the
statement.
"""

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from api.db.filters import apply_workflow_run_filters
from api.db.models import WorkflowRunModel


def _compile(filters):
    """The statement and its bound values.

    Not rendered with ``literal_binds``: a JSONB literal has no renderer, and
    the values are worth checking as values anyway.
    """
    query = apply_workflow_run_filters(select(WorkflowRunModel.id), filters)
    compiled = query.compile(dialect=postgresql.dialect())
    return str(compiled), list(compiled.params.values())


def _sql(filters):
    return _compile(filters)[0]


def _values(filters):
    return _compile(filters)[1]


def _outcome(codes, match=None):
    value = {"codes": codes}
    if match is not None:
        value["match"] = match
    return [{"attribute": "callOutcome", "type": "multiSelect", "value": value}]


class TestMatching:
    def test_any_is_the_default(self):
        """Somebody ticking two boxes means "either", until they say otherwise."""
        sql = _sql(_outcome(["booked", "callback"]))
        assert " OR " in sql
        assert " AND " not in sql.split("WHERE", 1)[1]

    def test_all_narrows(self):
        """The calls that booked *and* asked for a follow-up."""
        sql = _sql(_outcome(["booked", "callback"], match="all"))
        assert " AND " in sql.split("WHERE", 1)[1]

    def test_an_unrecognised_match_is_treated_as_any(self):
        """A stale client should widen the result, not silently empty it."""
        assert " OR " in _sql(_outcome(["booked", "callback"], match="either"))

    def test_reads_the_annotation_the_classifier_writes(self):
        sql, values = _compile(_outcome(["booked"]))
        assert "annotations" in sql
        assert "disposition" in values
        assert "dispositions" in values
        assert ["booked"] in values

    def test_uses_containment_rather_than_equality(self):
        """The column holds a list. Equality would match only a call with
        exactly one outcome, which is the case this feature exists to stop
        assuming."""
        assert "@>" in _sql(_outcome(["booked"]))

    def test_the_or_does_not_swallow_the_other_filters(self):
        """`a OR b AND c` is not what anybody ticking two boxes meant.

        The OR has to be bracketed, or a second filter on the same query stops
        applying to half the rows.
        """
        sql = _sql(
            [
                {"attribute": "runId", "type": "number", "value": {"value": 7}},
                {
                    "attribute": "callOutcome",
                    "type": "multiSelect",
                    "value": {"codes": ["booked", "callback"]},
                },
            ]
        )
        where = sql.split("WHERE", 1)[1]
        # Each code is its own bracketed term, and the whole OR group is
        # bracketed inside the AND that joins it to the other filter.
        assert ") OR (" in where
        assert "AND ((" in where


class TestDoingNothing:
    def test_no_codes_selected_does_not_filter(self):
        """An empty selection is a filter being built, not a request for
        nothing."""
        assert "WHERE" not in _sql(_outcome([]))

    def test_leaves_the_other_disposition_filter_alone(self):
        """`dispositionCode` is how the call ended, and is a different field.

        A call is legitimately both `user_hangup` and `booked`; the two filters
        have to be able to run together without one shadowing the other.
        """
        sql = _sql(
            [
                {
                    "attribute": "dispositionCode",
                    "type": "multiSelect",
                    "value": {"codes": ["user_hangup"]},
                },
                {
                    "attribute": "callOutcome",
                    "type": "multiSelect",
                    "value": {"codes": ["booked"]},
                },
            ]
        )
        assert "gathered_context" in sql
        assert "annotations" in sql
        values = _values(
            [
                {
                    "attribute": "dispositionCode",
                    "type": "multiSelect",
                    "value": {"codes": ["user_hangup"]},
                },
                {
                    "attribute": "callOutcome",
                    "type": "multiSelect",
                    "value": {"codes": ["booked"]},
                },
            ]
        )
        assert "mapped_call_disposition" in values
        assert "dispositions" in values
