"""The chat may reword a bot, and still may never change what is answering.

``revise_agent_facts`` can only re-fill a template's blanks, so it refuses
every agent not built from a template here -- which is most of the agents
somebody wants to reword. Until now the chat's answer to "make it stop
offering a discount" was to tell them to go and edit it by hand.

This is the other half: the wording itself, on the same boundary. A draft is
written, a person publishes, and the clinic's phone keeps being answered
exactly as it was until they do.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.services.agent_builder import tools
from api.services.agent_builder.tools import TOOL_NAMES, dispatch

GREETING = "Hello, thanks for calling Sunrise Clinic."
PROMPT = "Book callers in. Offer the ten percent discount."


def _definition():
    return {
        "nodes": [
            {"id": "s", "type": "startCall", "data": {"greeting": GREETING}},
            {"id": "g", "type": "globalNode", "data": {"prompt": "Warm."}},
            {"id": "a", "type": "agentNode", "data": {"prompt": PROMPT}},
        ],
        "edges": [],
    }


def _workflow(definition=None):
    return SimpleNamespace(
        id=1, name="Clinic front desk", workflow_definition=definition or _definition()
    )


#: Distinguishes "no workflow, on purpose" from "whatever the default is".
_DEFAULT = object()


async def _reword(arguments, *, workflow=_DEFAULT):
    with (
        patch.object(
            tools.db_client,
            "get_workflow",
            AsyncMock(return_value=workflow if workflow is not None else _workflow()),
        ) as get,
        patch.object(tools.db_client, "update_workflow", AsyncMock()) as update,
        patch.object(
            tools, "ReactFlowDTO", SimpleNamespace(model_validate=lambda d: d)
        ),
        patch.object(tools, "WorkflowGraph", lambda d: None),
    ):
        result = await dispatch(
            "revise_agent_prompt",
            arguments,
            session=None,
            organization_id=7,
            user_id=3,
        )
    return result, get, update


class TestTheToolExists:
    def test_the_builder_can_reword(self):
        assert "revise_agent_prompt" in TOOL_NAMES

    def test_it_still_cannot_publish(self):
        assert "publish_agent" not in TOOL_NAMES


class TestItWritesADraftAndSaysSo:
    async def test_the_new_wording_lands_on_the_right_nodes(self):
        result, _, update = await _reword(
            {
                "workflow_id": 1,
                "system_prompt": "Book callers in. Offer no discount.",
                "first_message": "Sunrise Clinic, how can I help?",
            }
        )
        assert result["revised"] is True
        nodes = {
            node["type"]: node["data"]
            for node in update.await_args.kwargs["workflow_definition"]["nodes"]
        }
        assert nodes["agentNode"]["prompt"] == "Book callers in. Offer no discount."
        assert nodes["startCall"]["greeting"] == "Sunrise Clinic, how can I help?"
        # A greeting the runtime is to speak is text, not a recording that
        # does not exist.
        assert nodes["startCall"]["greeting_type"] == "text"
        # Untouched, because it was not named.
        assert nodes["globalNode"]["prompt"] == "Warm."

    async def test_it_returns_what_changed_so_the_change_can_be_read(self):
        """A reply that says "done" about words nobody has seen is not
        reviewable, and these are the words a business says to its
        customers."""
        result, _, _ = await _reword(
            {"workflow_id": 1, "system_prompt": "Offer no discount."}
        )
        assert result["changed"]["system_prompt"] == {
            "before": PROMPT,
            "after": "Offer no discount.",
        }

    async def test_it_says_the_live_agent_is_untouched(self):
        result, _, _ = await _reword({"workflow_id": 1, "persona": "Brisk."})
        assert any("draft" in step for step in result["next_steps"])
        assert any("still answering" in step for step in result["next_steps"])


class TestWhatItRefuses:
    async def test_it_will_not_reword_one_step_of_a_flow(self):
        """Rewording one of several steps from a chat that cannot show the
        others is how somebody breaks a branch they never saw."""
        definition = _definition()
        definition["nodes"].append({"id": "b", "type": "agentNode", "data": {}})
        result, _, update = await _reword(
            {"workflow_id": 1, "system_prompt": "New."},
            workflow=_workflow(definition),
        )
        assert "more than one step" in result["error"]
        update.assert_not_awaited()

    async def test_it_will_not_invent_a_node_to_hold_the_wording(self):
        definition = {
            "nodes": [{"id": "a", "type": "agentNode", "data": {}}],
            "edges": [],
        }
        result, _, update = await _reword(
            {"workflow_id": 1, "first_message": "Hello."},
            workflow=_workflow(definition),
        )
        assert "nowhere to put first_message" in result["error"]
        update.assert_not_awaited()

    async def test_blank_wording_is_not_a_change(self):
        result, _, update = await _reword({"workflow_id": 1, "system_prompt": "   "})
        assert "Nothing to change" in result["error"]
        update.assert_not_awaited()

    async def test_the_lookup_is_organisation_scoped(self):
        """A workflow_id a model produced is a request-supplied id."""
        result, get, update = await _reword(
            {"workflow_id": 999, "system_prompt": "New."}, workflow=None
        )
        assert get.await_args.kwargs["organization_id"] == 7
        assert "No agent" in result["error"]
        update.assert_not_awaited()
