"""Decibyl's conversation can be more than one.

The assistant's thread was defined by absence -- rows with neither a
workflow nor a folder -- so every organisation had exactly one conversation
with Decibyl, for ever. "Start a new chat" was not a button anybody could
add and older chats had nowhere to be listed, because there was nowhere to
put a second one.

``thread_id`` is nullable and nothing was backfilled. NULL is the thread the
account has always had; a new chat gets an id. That way the migration could
not lose a conversation, and every reader that has not been taught about
threads still sees exactly what it saw before -- which is the property worth
testing, because it is the one that breaks silently.
"""

from __future__ import annotations

from api.db.models import AgentEventModel


class TestTheColumnItself:
    def test_the_model_carries_a_thread_id(self):
        assert hasattr(AgentEventModel, "thread_id")

    def test_it_is_nullable(self):
        """Every row written before threads existed has none, and so does
        every row written by anything that has not been taught about them."""
        assert AgentEventModel.__table__.c.thread_id.nullable is True

    def test_the_read_path_is_indexed(self):
        """ "This account's rows in this thread, newest first" walks the whole
        history without it."""
        names = {ix.name for ix in AgentEventModel.__table__.indexes}
        assert "ix_agent_events_org_thread_at" in names

    def test_it_is_not_a_foreign_key(self):
        """A thread is not a row anywhere -- it is the name a set of events
        share. A table for it would be a write and a join for something with
        no fields of its own."""
        assert not AgentEventModel.__table__.c.thread_id.foreign_keys


class TestTheMigrationIsReversible:
    def test_it_declares_both_directions(self):
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "c3f81ba47d20_threads_on_the_assistant_timeline.py"
        )
        spec = importlib.util.spec_from_file_location("threads_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.down_revision == "b7e4d9a2c108"
        assert callable(module.upgrade)
        assert callable(module.downgrade)


class TestWhichRowsAThreadReads:
    """The filter is the whole feature; everything else is a name.

    Read off the compiled SQL rather than the source, because what matters
    is the predicate the database receives -- a test that greps the
    implementation passes just as happily when the predicate is built and
    never applied.
    """

    @staticmethod
    async def _sql(**kwargs) -> str:
        from unittest.mock import MagicMock, patch

        from api.db import db_client

        captured: dict[str, str] = {}

        class _Recorder:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, query):
                captured["sql"] = str(
                    query.compile(compile_kwargs={"literal_binds": True})
                )
                result = MagicMock()
                result.scalars.return_value.all.return_value = []
                return result

        with patch.object(db_client, "async_session", lambda: _Recorder()):
            await db_client.agent_events(organization_id=1, **kwargs)
        return captured["sql"]

    async def test_no_thread_named_reads_the_original(self):
        """A caller that has not been taught about threads keeps seeing what
        it always saw: the rows with no thread id."""
        sql = await self._sql(assistant_thread=True)
        assert "thread_id IS NULL" in sql

    async def test_naming_one_reads_only_that_one(self):
        sql = await self._sql(assistant_thread=True, thread_id="t-abc")
        assert "thread_id = 't-abc'" in sql
        assert "thread_id IS NULL" not in sql

    async def test_a_bot_timeline_is_not_filtered_by_thread(self):
        """Threads are Decibyl's. A bot's own history has a workflow id and
        must not gain a predicate that would hide all of it."""
        sql = await self._sql(workflow_id=7)
        # The column is in every SELECT list; what must be absent is a
        # predicate on it.
        assert "thread_id IS NULL" not in sql
        assert "thread_id =" not in sql
