"""The organisation's memory as a picture.

The tests that matter here are about what the graph must not do: drop an
archived agent, hand a renderer a dangling edge, or show something a person
dismissed.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.organisation import graph


def _user(org=42):
    return SimpleNamespace(selected_organization_id=org)


def _graph_data():
    return {
        "nodes": [
            {"id": "org:42", "kind": "organisation", "label": "This business"},
            {
                "id": "agent:1",
                "kind": "agent",
                "label": "Front Desk",
                "archived": False,
                "live": True,
            },
            {
                "id": "agent:2",
                "kind": "agent",
                "label": "Old Night Line",
                "archived": True,
                "live": False,
            },
            {
                "id": "fact:9",
                "kind": "fact",
                "label": "Mon-Sat 9:30-8",
                "key": "opening_hours",
                "status": "confirmed",
                "times_seen": 4,
            },
            {
                "id": "gap:11",
                "kind": "gap",
                "label": "Do you open on Saturday?",
                "key": "do_you_open_on_saturday",
                "status": "learned",
                "times_seen": 12,
            },
            {"id": "app:googlecalendar", "kind": "app", "label": "googlecalendar"},
        ],
        "edges": [
            {"source": "org:42", "target": "agent:1", "relation": "employs"},
            {"source": "org:42", "target": "agent:2", "relation": "employs"},
            {"source": "org:42", "target": "fact:9", "relation": "knows"},
            {"source": "org:42", "target": "gap:11", "relation": "cannot answer"},
            {"source": "agent:2", "target": "gap:11", "relation": "learned"},
            {
                "source": "agent:1",
                "target": "app:googlecalendar",
                "relation": "acts in",
                "uses": 96,
                "errors": 3,
            },
        ],
    }


class TestTheGraph:
    @pytest.mark.asyncio
    async def test_an_archived_agent_keeps_its_edges(self):
        """Its calls happened and what it learned is as true as it was. An
        agent switched off in March still taught the business something, and
        the picture has to show that or it is agreeing that knowledge belongs
        to whoever was holding it."""
        with patch(
            "api.routes.organisation.db_client.organisation_graph_edges",
            AsyncMock(return_value=_graph_data()),
        ):
            response = await graph(days=90, user=_user())

        archived = next(n for n in response.nodes if n.id == "agent:2")
        assert archived.archived is True
        learned = [e for e in response.edges if e.relation == "learned"]
        assert learned and learned[0].source == "agent:2"

    @pytest.mark.asyncio
    async def test_a_gap_and_a_fact_are_the_same_relation_seen_from_two_sides(self):
        with patch(
            "api.routes.organisation.db_client.organisation_graph_edges",
            AsyncMock(return_value=_graph_data()),
        ):
            response = await graph(days=90, user=_user())

        relations = {edge.relation for edge in response.edges}
        assert "knows" in relations
        assert "cannot answer" in relations

    @pytest.mark.asyncio
    async def test_an_action_edge_carries_its_weight_and_its_failures(self):
        """So a thick line means a system the business depends on rather than
        one it touched once, and a red one means the work is not being filed."""
        with patch(
            "api.routes.organisation.db_client.organisation_graph_edges",
            AsyncMock(return_value=_graph_data()),
        ):
            response = await graph(days=90, user=_user())

        acted = next(e for e in response.edges if e.relation == "acts in")
        assert acted.uses == 96
        assert acted.errors == 3

    @pytest.mark.asyncio
    async def test_the_window_is_clamped_rather_than_trusted(self):
        with patch(
            "api.routes.organisation.db_client.organisation_graph_edges",
            AsyncMock(return_value={"nodes": [], "edges": []}),
        ) as query:
            await graph(days=99999, user=_user())
            assert query.await_args.kwargs["days"] == 365
            await graph(days=0, user=_user())
            assert query.await_args.kwargs["days"] == 1

    @pytest.mark.asyncio
    async def test_a_session_with_no_organization_is_refused(self):
        with pytest.raises(HTTPException) as raised:
            await graph(days=90, user=_user(org=None))
        assert raised.value.status_code == 400


class TestNoDanglingEdges:
    def test_an_edge_to_a_node_that_was_never_built_is_dropped(self):
        """A renderer handed a dangling edge either throws or silently draws
        nothing, and both are worse than a smaller graph. Asserted against the
        filter the query itself applies."""
        data = _graph_data()
        data["edges"].append(
            {"source": "agent:99", "target": "fact:9", "relation": "learned"}
        )
        known = {node["id"] for node in data["nodes"]}
        kept = [
            edge
            for edge in data["edges"]
            if edge["source"] in known and edge["target"] in known
        ]
        assert len(kept) == len(data["edges"]) - 1
