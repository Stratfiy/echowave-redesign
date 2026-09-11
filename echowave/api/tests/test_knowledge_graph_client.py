"""A graph that is missing, broken or slow must cost a caller nothing.

These are the tests that matter more than any query this will ever serve.
Post-call processing reaches this code on every completed call, and the call
path will reach it later; if any of it can raise, the graph becomes a way to
drop calls in exchange for better answers, which is a bad trade at any quality.

The other half is the partition. An episode written without one is readable by
every other organization, so the write path refuses it even though the builders
cannot produce one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from api.services.knowledge_graph import client as client_module
from api.services.knowledge_graph import ingest as ingest_module
from api.services.knowledge_graph.episodes import Episode
from api.services.knowledge_graph.ingest import remember


def _episode(**overrides) -> Episode:
    kwargs = dict(
        name="call:1",
        body="Agent: hello. Caller: I need an appointment.",
        source_description="Phone call",
        reference_time=datetime(2026, 8, 14, tzinfo=UTC),
        group_id="org:42",
    )
    kwargs.update(overrides)
    return Episode(**kwargs)


@pytest.fixture(autouse=True)
async def _forget_the_cached_client():
    await client_module.reset_for_tests()
    yield
    await client_module.reset_for_tests()


@pytest.fixture(autouse=True)
def _no_library_needed():
    """The two functions that name graphiti_core, stubbed.

    Everything else in this package is ours, and testing it should not depend
    on a graph library being installed -- which is also true of a deployment
    that has no graph.
    """
    with patch.object(ingest_module, "episode_source_type", return_value="message"):
        yield


class TestADeploymentWithNoGraph:
    """Every self-hosted install and every local checkout, by default."""

    async def test_there_is_no_client(self):
        with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", None):
            assert await client_module.get_graph() is None

    async def test_it_reports_itself_unconfigured(self):
        with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", None):
            assert client_module.graph_is_configured() is False

    async def test_remembering_is_a_quiet_no(self):
        with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", None):
            assert await remember(_episode()) is False


class TestAGraphThatIsConfiguredButBroken:
    async def test_a_server_that_will_not_connect_does_not_raise(self):
        with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://nowhere"):
            with patch.object(
                client_module,
                "_construct_graphiti",
                side_effect=OSError("connection refused"),
            ):
                assert await client_module.get_graph() is None

    async def test_it_does_not_retry_the_failure_on_every_call(self):
        """A deployment with a wrong URL would otherwise reconnect per call."""
        with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://nowhere"):
            with patch.object(
                client_module, "_construct_graphiti", side_effect=OSError("refused")
            ) as constructor:
                await client_module.get_graph()
                await client_module.get_graph()
                await client_module.get_graph()
                assert constructor.call_count == 1

    async def test_an_extraction_that_fails_is_not_an_error_to_the_caller(self):
        graph = AsyncMock()
        graph.add_episode.side_effect = RuntimeError("model refused")
        with patch.object(client_module, "_client", graph):
            with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"):
                assert await remember(_episode()) is False


class TestWhatIsActuallyWritten:
    async def test_the_partition_reaches_graphiti(self):
        graph = AsyncMock()
        with patch.object(client_module, "_client", graph):
            with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"):
                assert await remember(_episode(group_id="org:7")) is True
        assert graph.add_episode.await_args.kwargs["group_id"] == "org:7"

    async def test_the_event_time_reaches_graphiti_not_now(self):
        graph = AsyncMock()
        happened = datetime(2026, 8, 14, tzinfo=UTC)
        with patch.object(client_module, "_client", graph):
            with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"):
                await remember(_episode(reference_time=happened))
        assert graph.add_episode.await_args.kwargs["reference_time"] == happened

    async def test_an_episode_with_no_partition_is_refused(self):
        """Unreachable through the builders. Refused anyway."""
        graph = AsyncMock()
        with patch.object(client_module, "_client", graph):
            with patch.object(client_module, "KNOWLEDGE_GRAPH_URL", "falkor://here"):
                assert await remember(_episode(group_id="")) is False
        graph.add_episode.assert_not_awaited()
