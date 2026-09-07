"""Which agents a handoff is allowed to name.

The splicer cannot answer this — it has no session and no idea who is calling —
so it delegates by asking for a loader and treating a missing member as a
refusal. That makes this module the only thing standing between a handoff and
another account's prompts, tools and conversation being spliced into a call.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.squad import HANDOFF, SquadError


def _node(node_id, node_type, **data):
    return {"id": node_id, "type": node_type, "data": data}


def _edge(source, target):
    return {"id": f"{source}-{target}", "source": source, "target": target}


def _member(prefix="m"):
    return {
        "nodes": [
            _node(f"{prefix}-start", "startCall", greeting="Hi"),
            _node(f"{prefix}-1", "agentNode", prompt="Take the payment"),
        ],
        "edges": [_edge(f"{prefix}-start", f"{prefix}-1")],
    }


def _parent(reference="member-uuid"):
    return {
        "nodes": [
            _node("start", "startCall", greeting="Hello"),
            _node("reception", "agentNode", prompt="What do they want?"),
            _node("h", HANDOFF, agent_uuid=reference),
        ],
        "edges": [_edge("start", "reception"), _edge("reception", "h")],
    }


class _Definition:
    def __init__(self, workflow_json):
        self.workflow_json = workflow_json


class _Workflow:
    def __init__(self, released=None, current=None):
        self.released_definition = _Definition(released) if released else None
        self.current_definition = _Definition(current) if current else None


def _client(by_org):
    """Stand in for the org-scoped getter, keyed by (uuid, organization_id)."""

    async def get_workflow_by_uuid(workflow_uuid, organization_id):
        return by_org.get((workflow_uuid, organization_id))

    return AsyncMock(side_effect=get_workflow_by_uuid)


async def _assemble(parent, by_org, *, organization_id=1):
    from api.services.workflow import squad_loader

    with patch.object(squad_loader.db_client, "get_workflow_by_uuid", _client(by_org)):
        return await squad_loader.assemble_for_run(
            parent, organization_id=organization_id
        )


class TestTenantIsolation:
    async def test_an_agent_in_this_account_is_spliced_in(self):
        graph = await _assemble(
            _parent(), {("member-uuid", 1): _Workflow(released=_member())}
        )
        assert "h__m-1" in {n["id"] for n in graph["nodes"]}

    async def test_an_agent_in_another_account_is_refused(self):
        """Not skipped — refused.

        Splicing nothing would drop a step out of the conversation with
        nothing to show for it; splicing it in would hand this account
        somebody else's prompts and tools.
        """
        with pytest.raises(SquadError, match="another account"):
            await _assemble(
                _parent(),
                {("member-uuid", 2): _Workflow(released=_member())},
                organization_id=1,
            )

    async def test_the_lookup_is_always_scoped(self):
        """A getter called without an org would defeat everything above."""
        from api.services.workflow import squad_loader

        client = _client({("member-uuid", 1): _Workflow(released=_member())})
        with patch.object(squad_loader.db_client, "get_workflow_by_uuid", client):
            await squad_loader.assemble_for_run(_parent(), organization_id=1)

        for call in client.await_args_list:
            assert call.args[1] == 1 or call.kwargs.get("organization_id") == 1


class TestWhichVersion:
    async def test_the_released_one(self):
        """A member is reused by other agents, so half-finished edits to it
        must not reach a live call through somebody else's squad."""
        workflow = _Workflow(released=_member("released"), current=_member("draft"))
        graph = await _assemble(_parent(), {("member-uuid", 1): workflow})
        ids = {n["id"] for n in graph["nodes"]}
        assert "h__released-1" in ids
        assert "h__draft-1" not in ids

    async def test_falls_back_to_the_draft_when_nothing_is_published(self):
        """An agent built and never published is still a real agent, and
        refusing it would look like the handoff was broken."""
        workflow = _Workflow(current=_member("draft"))
        graph = await _assemble(_parent(), {("member-uuid", 1): workflow})
        assert "h__draft-1" in {n["id"] for n in graph["nodes"]}

    async def test_an_agent_with_no_definition_at_all_is_refused(self):
        with pytest.raises(SquadError):
            await _assemble(_parent(), {("member-uuid", 1): _Workflow()})


class TestNotDoingWork:
    async def test_a_workflow_with_no_handoffs_makes_no_queries(self):
        """Almost every call. It must cost one scan and nothing else."""
        from api.services.workflow import squad_loader

        client = _client({})
        with patch.object(squad_loader.db_client, "get_workflow_by_uuid", client):
            plain = _member()
            assert (
                await squad_loader.assemble_for_run(plain, organization_id=1) is plain
            )
        client.assert_not_awaited()

    async def test_the_same_agent_behind_two_handoffs_is_fetched_once(self):
        from api.services.workflow import squad_loader

        parent = _parent()
        parent["nodes"].append(_node("h2", HANDOFF, agent_uuid="member-uuid"))
        parent["edges"].append(_edge("reception", "h2"))

        client = _client({("member-uuid", 1): _Workflow(released=_member())})
        with patch.object(squad_loader.db_client, "get_workflow_by_uuid", client):
            await squad_loader.assemble_for_run(parent, organization_id=1)

        assert client.await_count == 1
