"""The graph is wired in, and nothing that feeds it can break its caller.

Family B's substrate: FalkorDB reached through one URL, extraction and
embedding on the platform's own OpenAI key, a search that returns what the
graph holds with the record's confirmed/inferred overlay laid over it, and
three feeders (a finished call, a Home-thread exchange, a channel document)
that return quietly when there is no graph and never raise when there is.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.db import db_client
from api.services.knowledge_graph import client as client_module
from api.services.knowledge_graph import feed
from api.services.knowledge_graph.client import Fact, parse_graph_url, search_facts

AT = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


class TestTheGraphUrl:
    def test_falkor_with_password_and_database(self):
        target = parse_graph_url("falkor://:secret@falkordb:6379/decibyl")
        assert target.scheme == "falkor"
        assert target.host == "falkordb"
        assert target.port == 6379
        assert target.password == "secret"
        assert target.database == "decibyl"

    def test_falkor_defaults(self):
        target = parse_graph_url("falkor://localhost")
        assert target.port == 6379
        assert target.password is None
        assert target.database == "default_db"

    def test_neo4j_passes_through_untouched(self):
        target = parse_graph_url("bolt://neo4j:7687")
        assert target.scheme == "bolt"
        assert target.uri == "bolt://neo4j:7687"


class TestSearchingTheGraph:
    async def test_no_graph_means_none_not_an_empty_answer(self):
        with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", None):
            assert await search_facts(7, "who owes us money") is None

    async def test_facts_come_back_newest_first_within_the_partition(self):
        graph = AsyncMock()
        graph.search.return_value = [
            SimpleNamespace(
                uuid="a",
                fact="Ravi agreed to pay by Friday",
                valid_at=datetime(2026, 8, 1, tzinfo=UTC),
                invalid_at=None,
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
                episodes=["e1"],
            ),
            SimpleNamespace(
                uuid="b",
                fact="Ravi runs Sharma Traders",
                valid_at=datetime(2026, 8, 20, tzinfo=UTC),
                invalid_at=None,
                created_at=datetime(2026, 8, 20, tzinfo=UTC),
                episodes=["e2"],
            ),
        ]
        with (
            patch.object(client_module, "_client", graph),
            patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"),
            patch.object(
                client_module, "_overlay_confirmed", side_effect=lambda _o, f: f
            ),
        ):
            facts = await search_facts(
                7, "Ravi", since=datetime(2026, 7, 1, tzinfo=UTC)
            )
        assert [f.uuid for f in facts] == ["b", "a"]
        assert graph.search.await_args.kwargs["group_ids"] == ["org:7"]

    async def test_a_time_window_drops_what_is_outside_it(self):
        graph = AsyncMock()
        graph.search.return_value = [
            SimpleNamespace(
                uuid="old",
                fact="old",
                valid_at=datetime(2026, 1, 1, tzinfo=UTC),
                invalid_at=None,
                created_at=None,
                episodes=[],
            )
        ]
        with (
            patch.object(client_module, "_client", graph),
            patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"),
        ):
            facts = await search_facts(7, "x", since=datetime(2026, 6, 1, tzinfo=UTC))
        assert facts == []

    async def test_a_graph_that_fails_to_search_is_none_not_an_error(self):
        graph = AsyncMock()
        graph.search.side_effect = RuntimeError("down")
        with (
            patch.object(client_module, "_client", graph),
            patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"),
        ):
            assert await search_facts(7, "x") is None

    async def test_only_the_record_confers_confirmed(self):
        """A graph fact is 'inferred' until a confirmed fact in Postgres says
        the same thing. The graph never promotes itself."""
        facts = [
            Fact("a", "GST number is 29ABCDE1234F1Z5", AT, None, AT, ()),
            Fact("b", "Ravi likes tea", AT, None, AT, ()),
        ]
        rows = [SimpleNamespace(value="29ABCDE1234F1Z5"), SimpleNamespace(value="no")]
        with patch.object(
            db_client, "organisation_memory", AsyncMock(return_value=rows)
        ):
            out = await client_module._overlay_confirmed(7, facts)
        assert [f.status for f in out] == ["confirmed", "inferred"]

    async def test_a_record_that_cannot_be_read_leaves_everything_inferred(self):
        facts = [Fact("a", "anything", AT, None, AT, ())]
        with patch.object(
            db_client, "organisation_memory", AsyncMock(side_effect=OSError("db"))
        ):
            out = await client_module._overlay_confirmed(7, facts)
        assert out[0].status == "inferred"

    def test_a_fact_reads_as_dates_not_timestamps(self):
        fact = Fact("a", "x", AT, None, AT, (), status="confirmed")
        assert fact.as_dict() == {
            "fact": "x",
            "status": "confirmed",
            "from": "2026-09-01",
            "until": None,
            "recorded": "2026-09-01",
        }


class TestBuildingTheClient:
    async def test_refuses_without_a_platform_openai_key(self):
        import pytest

        with patch.object(
            client_module, "_platform_openai_key", AsyncMock(return_value=None)
        ):
            with pytest.raises(RuntimeError):
                await client_module._construct_graphiti("falkor://here")


class TestFeedingTheGraph:
    """Each feeder is called from inside something that already succeeded:
    a finished call, a posted reply, a filed document. None may raise."""

    async def test_nothing_happens_without_a_graph(self):
        with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", None):
            assert (
                await feed.remember_call(
                    organization_id=7,
                    workflow_run_id=1,
                    agent_name="Bot",
                    transcript="Agent: hi\nCaller: hello",
                    happened_at=AT,
                )
                is False
            )
            assert (
                await feed.remember_exchange(
                    organization_id=7,
                    person_said="Ravi said he will pay the pending amount by Friday.",
                    decibyl_said="Noted.",
                )
                is False
            )
            assert (
                await feed.remember_document(
                    organization_id=7, document_uuid="u", filename="f.pdf", chunks=["a"]
                )
                == 0
            )

    async def test_a_call_becomes_one_episode_in_the_account_partition(self):
        remember = AsyncMock(return_value=True)
        with (
            patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"),
            patch.object(feed.ingest, "remember", remember),
        ):
            assert (
                await feed.remember_call(
                    organization_id=7,
                    workflow_run_id=1,
                    agent_name="Bot",
                    transcript=(
                        "Agent: Hello, this is Sharma Traders. Caller: Hi, I want the "
                        "GST invoice for August, and the pending amount is due Friday. "
                        "Agent: Noted, I will send the invoice today."
                    ),
                    happened_at=AT,
                )
                is True
            )
        episode = remember.await_args.args[0]
        assert episode.group_id == "org:7"
        assert episode.reference_time == AT

    async def test_a_short_thread_line_teaches_nothing(self):
        remember = AsyncMock(return_value=True)
        with (
            patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"),
            patch.object(feed.ingest, "remember", remember),
        ):
            assert (
                await feed.remember_exchange(
                    organization_id=7, person_said="thanks", decibyl_said="ok"
                )
                is False
            )
        remember.assert_not_awaited()

    async def test_a_thread_exchange_keeps_both_sides_with_time(self):
        remember = AsyncMock(return_value=True)
        with (
            patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"),
            patch.object(feed.ingest, "remember", remember),
        ):
            assert (
                await feed.remember_exchange(
                    organization_id=7,
                    person_said="Ravi from Sharma Traders agreed to pay by Friday.",
                    decibyl_said="Noted; I will remind you on Thursday.",
                    at=AT,
                    channel="whatsapp",
                )
                is True
            )
        episode = remember.await_args.args[0]
        assert "Person: Ravi from Sharma Traders" in episode.body
        assert "Decibyl: Noted" in episode.body
        assert episode.reference_time == AT
        assert episode.group_id == "org:7"
        assert "whatsapp" in episode.source_description

    async def test_a_document_is_one_episode_per_chunk(self):
        remember = AsyncMock(return_value=True)
        with (
            patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"),
            patch.object(feed.ingest, "remember", remember),
        ):
            taken = await feed.remember_document(
                organization_id=7,
                document_uuid="abc",
                filename="LIC policy.pdf",
                chunks=["Policy number 123", "Premium due 1 March"],
                published_at=AT,
            )
        assert taken == 2
        assert {c.args[0].group_id for c in remember.await_args_list} == {"org:7"}

    async def test_a_graph_that_throws_costs_the_caller_nothing(self):
        with (
            patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"),
            patch.object(
                feed.ingest, "remember", AsyncMock(side_effect=RuntimeError("x"))
            ),
        ):
            assert (
                await feed.remember_call(
                    organization_id=7,
                    workflow_run_id=1,
                    agent_name="Bot",
                    transcript="Agent: hi\nCaller: a long enough line to be kept here.",
                    happened_at=AT,
                )
                is False
            )
            assert (
                await feed.remember_exchange(
                    organization_id=7,
                    person_said="Ravi from Sharma Traders agreed to pay by Friday.",
                    decibyl_said="Noted.",
                )
                is False
            )
            assert (
                await feed.remember_document(
                    organization_id=7, document_uuid="u", filename="f.pdf", chunks=["a"]
                )
                == 0
            )
