"""A bot's own memory, and the unique index that keeps it from eating the
organisation's.

``organisation_facts`` now carries a nullable ``workflow_id``: NULL is the
organisation's memory, which every bot reads, and a workflow id is one bot's
own. The interesting tests are not about the column. They are about the index,
because this change had one way to go wrong and it was silent.

**The trap.** In Postgres, NULLs are distinct in a unique index. Adding
``workflow_id`` to the existing four-column unique index would have stopped it
constraining organisation facts at all -- two rows with a NULL workflow and the
same subject and key would no longer collide, so every repeated fact about the
business would quietly become a second row. No error, no symptom, until a
prompt filled with duplicates. That is the shape ``api/AGENTS.md`` is written
about, and it is why the schema has two *partial* unique indexes instead.

**The second trap, one layer down.** A partial index can only be inferred by an
``ON CONFLICT`` clause that reproduces its predicate. An upsert that names no
predicate matches no index and Postgres refuses it -- loudly, which is fine.
But an upsert whose predicate renders with a *bound parameter* fails to match
while looking correct in the source, which is not fine. So these tests compare
the rendered SQL against the declared index, rather than trusting that the two
were written to agree.

Neither needs a database: the schema and the compiled statement are both
available without one, and they are the two halves of the contract.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.models import OrganisationFactModel
from api.db.organisation_fact_client import BOT_SCOPE, ORG_SCOPE, _scope
from api.services.workflow import organisation_memory as memory

CLIENT_SOURCE = (
    Path(__file__).resolve().parent.parent / "db" / "organisation_fact_client.py"
)

ORG_INDEX = "uq_organisation_facts_subject_key"
BOT_INDEX = "uq_organisation_facts_bot_subject_key"


def _indexes() -> dict[str, object]:
    return {index.name: index for index in OrganisationFactModel.__table__.indexes}


def _predicate(index) -> str:
    return str(index.dialect_options["postgresql"]["where"]).strip()


def _columns(index) -> list[str]:
    return [column.name for column in index.columns]


def _rendered_conflict(workflow_id) -> str:
    """The ON CONFLICT clause an upsert at this scope actually sends."""
    index_elements, index_where = _scope(workflow_id)
    statement = pg_insert(OrganisationFactModel).values(
        [
            {
                "organization_id": 1,
                "workflow_id": workflow_id,
                "subject_type": "organisation",
                "subject_key": "self",
                "key": "opening_hours",
                "value": "9 to 6",
            }
        ]
    )
    statement = statement.on_conflict_do_update(
        index_elements=index_elements,
        index_where=index_where,
        set_={"value": statement.excluded.value},
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))
    return sql.split("ON CONFLICT")[1].split("DO UPDATE")[0].strip()


class TestTheIndexStillConstrainsTheOrganisation:
    """The half of the change that had no symptom if it went wrong."""

    def test_the_organisation_index_is_partial_on_a_null_workflow(self):
        """Without the predicate this index would constrain every row and a bot
        fact could never share a subject and key with an organisation fact --
        which is the entire feature."""
        assert _predicate(_indexes()[ORG_INDEX]) == "workflow_id IS NULL"

    def test_the_organisation_index_keeps_its_original_four_columns(self):
        """A second call saying the same thing must still update one row rather
        than write a second. This is the constraint that existed before bot
        scope and must survive it unchanged."""
        assert _columns(_indexes()[ORG_INDEX]) == [
            "organization_id",
            "subject_type",
            "subject_key",
            "key",
        ]

    def test_the_bot_index_is_partial_the_other_way_and_carries_the_workflow(self):
        index = _indexes()[BOT_INDEX]
        assert _predicate(index) == "workflow_id IS NOT NULL"
        assert _columns(index) == [
            "organization_id",
            "workflow_id",
            "subject_type",
            "subject_key",
            "key",
        ]

    def test_both_scopes_are_unique(self):
        """A non-unique index here would be a plain lookup index that looks
        right in a migration and constrains nothing."""
        assert _indexes()[ORG_INDEX].unique
        assert _indexes()[BOT_INDEX].unique

    def test_no_unique_index_spans_both_scopes(self):
        """The whole point. One unique index over a nullable workflow_id is the
        bug this schema exists to avoid, and it would pass every other test
        here."""
        for name, index in _indexes().items():
            if not index.unique:
                continue
            assert index.dialect_options["postgresql"]["where"] is not None, (
                f"{name} is unique across both scopes -- NULLs are distinct in "
                "Postgres, so it cannot constrain organisation facts"
            )


class TestTheUpsertCanActuallyFindTheIndex:
    """A predicate that does not match its index is refused by Postgres, and a
    predicate that renders with a bound parameter does not match."""

    def test_an_organisation_upsert_names_the_organisation_index(self):
        clause = _rendered_conflict(None)
        assert clause == (
            "(organization_id, subject_type, subject_key, key) "
            "WHERE workflow_id IS NULL"
        )

    def test_a_bot_upsert_names_the_bot_index(self):
        clause = _rendered_conflict(7)
        assert clause == (
            "(organization_id, workflow_id, subject_type, subject_key, key) "
            "WHERE workflow_id IS NOT NULL"
        )

    @pytest.mark.parametrize("workflow_id", [None, 7])
    def test_no_bound_parameter_reaches_the_predicate(self, workflow_id):
        """``coalesce(workflow_id, 0)`` would have rendered the 0 as a bound
        parameter, which infers nothing while reading correctly in the source.
        ``IS NULL`` renders as itself."""
        predicate = _rendered_conflict(workflow_id).split("WHERE")[1]
        assert "%(" not in predicate and ":" not in predicate

    @pytest.mark.parametrize(
        "scope, expected",
        [(ORG_SCOPE, "workflow_id IS NULL"), (BOT_SCOPE, "workflow_id IS NOT NULL")],
    )
    def test_the_scope_constants_render_as_the_index_predicates(self, scope, expected):
        """The constants and the schema are written in two files and have to
        agree. Compared rather than assumed, because nothing else would notice
        if one were edited."""
        rendered = str(scope.compile(dialect=postgresql.dialect()))
        assert rendered.replace("organisation_facts.", "") == expected


class TestEveryUpsertNamesItsScope:
    """An AST guard, in the shape of ``test_silent_absence_guard.py``.

    A new ``on_conflict_do_update`` written without ``index_where`` matches no
    partial index. Postgres refuses it, so this is not silent -- but it fails at
    runtime on a write path, which for this table means a post-call task, which
    means a log line nobody reads and a fact quietly not remembered.
    """

    def test_no_upsert_omits_index_where(self):
        tree = ast.parse(CLIENT_SOURCE.read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "on_conflict_do_update"
        ]
        assert calls, "expected upserts in the fact client; did the module move?"
        for call in calls:
            keywords = {keyword.arg for keyword in call.keywords}
            assert "index_where" in keywords, (
                f"on_conflict_do_update at line {call.lineno} names no "
                "index_where, so it can match neither partial unique index"
            )


class TestTheBotsAnswerWins:
    """The read rule, and the direction is the whole argument.

    The organisation layer is the default every bot inherits; the bot layer is
    that bot having been told otherwise. If the organisation won, a bot's own
    memory could never say anything and the column would be decoration.
    """

    @staticmethod
    def _row(key, value, workflow_id=None):
        return SimpleNamespace(key=key, value=value, workflow_id=workflow_id)

    @pytest.mark.asyncio
    async def test_a_bot_fact_beats_the_organisation_fact_on_the_same_key(self):
        rows = [
            self._row("language", "English"),
            self._row("language", "Hindi", workflow_id=7),
        ]
        with patch.object(
            memory.db_client, "organisation_memory", AsyncMock(return_value=rows)
        ):
            recalled = await memory.recall_for_bot(organization_id=42, workflow_id=7)
        assert recalled["language"] == "Hindi"

    @pytest.mark.asyncio
    async def test_the_order_rows_arrive_in_does_not_decide_it(self):
        """The database returns rows in whatever order it likes. Sorting by
        scope is what makes the rule hold, so the reversed list must give the
        same answer."""
        rows = [
            self._row("language", "Hindi", workflow_id=7),
            self._row("language", "English"),
        ]
        with patch.object(
            memory.db_client, "organisation_memory", AsyncMock(return_value=rows)
        ):
            recalled = await memory.recall_for_bot(organization_id=42, workflow_id=7)
        assert recalled["language"] == "Hindi"

    @pytest.mark.asyncio
    async def test_the_organisations_facts_are_still_inherited(self):
        """A bot reads the house rules it was never told about. Otherwise every
        bot starts blank, which is the failure this table exists to end."""
        rows = [self._row("closed_on", "Sunday"), self._row("language", "Hindi", 7)]
        with patch.object(
            memory.db_client, "organisation_memory", AsyncMock(return_value=rows)
        ):
            recalled = await memory.recall_for_bot(organization_id=42, workflow_id=7)
        assert recalled == {"closed_on": "Sunday", "language": "Hindi"}

    @pytest.mark.asyncio
    async def test_only_confirmed_facts_are_read(self):
        """The gate that makes this whole table safe to write to on every call.
        A bot's own scope does not get to be the exception to it."""
        recorded = AsyncMock(return_value=[])
        with patch.object(memory.db_client, "organisation_memory", recorded):
            await memory.recall_for_bot(organization_id=42, workflow_id=7)
        assert recorded.await_args.kwargs["status"] == "confirmed"
        assert recorded.await_args.kwargs["kind"] == "fact"

    @pytest.mark.asyncio
    async def test_a_failure_to_read_memory_is_empty_rather_than_an_exception(self):
        """Same posture as the rest of this module: a bot with no memory takes
        the call. A bot that raises does not."""
        with patch.object(
            memory.db_client,
            "organisation_memory",
            AsyncMock(side_effect=RuntimeError("database on fire")),
        ):
            assert await memory.recall_for_bot(organization_id=42, workflow_id=7) == {}


class TestWhatACallLearnsStaysWithTheBusiness:
    """A caller's own facts are never scoped to a bot.

    The clinic's patients are the clinic's, not the property of whichever bot
    picked up. Scoping them per bot would make the second bot ask every
    question again -- exactly the failure organisation_memory exists to end.
    """

    @pytest.mark.asyncio
    async def test_promoting_a_call_writes_no_workflow_id(self):
        remembered = AsyncMock(return_value=1)
        with patch.object(memory.db_client, "remember_facts", remembered):
            await memory.promote_from_run(
                organization_id=42,
                workflow_run_id=9,
                gathered_context={"extracted_variables": {"name": "Asha"}},
                subject_key="+919000000000",
            )
        assert remembered.await_args.kwargs.get("workflow_id") is None
