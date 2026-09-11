"""The chat may now change an agent, and may never change what is answering.

The builder could create and nothing else. Its own catalogue explains why:
"a chat that can silently rewrite the agent answering a clinic's phone is a
chat one bad turn away from an outage."

That reasoning holds, and it does not require refusing to edit -- it requires
refusing to *publish*. The platform already separates a draft from the live
version, and a person publishes. So the chat writes a draft, says so, and the
clinic's phone keeps being answered exactly as it was.

The other half is provenance. Revision re-fills the answers a template was
built with, so those answers have to have been kept. They were not.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.services.agent_builder import tools
from api.services.agent_builder.tools import (
    PROVENANCE_TEMPLATE_KEY,
    TOOL_NAMES,
    dispatch,
)


def _workflow(wid=1, name="Clinic front desk", stored=None):
    return SimpleNamespace(id=wid, name=name, template_context_variables=stored)


class TestTheToolsExist:
    def test_the_builder_can_see_what_exists(self):
        assert "list_my_agents" in TOOL_NAMES

    def test_the_builder_can_revise_facts(self):
        assert "revise_agent_facts" in TOOL_NAMES

    def test_it_still_cannot_buy_a_number_or_write_code(self):
        """The exclusions that were the point of the catalogue."""
        assert "buy_phone_number" not in TOOL_NAMES
        assert "publish_agent" not in TOOL_NAMES
        assert "write_workflow" not in TOOL_NAMES


class TestItNeverTouchesTheLiveAgent:
    async def test_a_revision_says_it_is_only_a_draft(self):
        stored = {PROVENANCE_TEMPLATE_KEY: "t1", "clinic_name": "Old"}
        built = SimpleNamespace(
            definition={"nodes": [], "edges": []}, missing_variables=[], name="x"
        )
        with (
            patch.object(
                tools.db_client,
                "get_workflow",
                AsyncMock(return_value=_workflow(stored=stored)),
            ),
            patch.object(tools.db_client, "update_workflow", AsyncMock()) as update,
            patch.object(tools, "get_template", lambda _t: object()),
            patch.object(tools, "assemble", lambda *a, **k: built),
            patch.object(
                tools, "ReactFlowDTO", SimpleNamespace(model_validate=lambda d: d)
            ),
            patch.object(tools, "WorkflowGraph", lambda d: None),
        ):
            result = await dispatch(
                "revise_agent_facts",
                {"workflow_id": 1, "variables": {"clinic_name": "New"}},
                session=None,
                organization_id=7,
                user_id=3,
            )
        assert result["revised"] is True
        assert any("draft" in step for step in result["next_steps"])
        assert any("still answering" in step for step in result["next_steps"])
        # The definition is written; nothing publishes.
        assert update.await_args.kwargs["workflow_definition"] is built.definition


class TestItCannotReachAnotherTenantsAgent:
    async def test_the_lookup_is_organisation_scoped(self):
        """A workflow_id a model produced is a request-supplied id."""
        with (
            patch.object(
                tools.db_client, "get_workflow", AsyncMock(return_value=None)
            ) as get,
            patch.object(tools.db_client, "update_workflow", AsyncMock()) as update,
        ):
            result = await dispatch(
                "revise_agent_facts",
                {"workflow_id": 999, "variables": {"a": "b"}},
                session=None,
                organization_id=7,
                user_id=3,
            )
        assert get.await_args.kwargs["organization_id"] == 7
        assert "No agent" in result["error"]
        update.assert_not_awaited()

    async def test_listing_is_organisation_scoped(self):
        with patch.object(
            tools.db_client, "get_all_workflows", AsyncMock(return_value=[])
        ) as listed:
            await dispatch(
                "list_my_agents", {}, session=None, organization_id=7, user_id=3
            )
        assert listed.await_args.kwargs["organization_id"] == 7


class TestWhatItRefuses:
    async def test_an_agent_with_no_provenance_cannot_be_revised(self):
        """Built on the canvas, or before provenance was recorded."""
        with (
            patch.object(
                tools.db_client,
                "get_workflow",
                AsyncMock(return_value=_workflow(stored={})),
            ),
            patch.object(tools.db_client, "update_workflow", AsyncMock()) as update,
        ):
            result = await dispatch(
                "revise_agent_facts",
                {"workflow_id": 1, "variables": {"a": "b"}},
                session=None,
                organization_id=7,
                user_id=3,
            )
        assert "not built from a template" in result["error"]
        assert "/workflow/1" in result["error"], "point them somewhere useful"
        update.assert_not_awaited()

    async def test_an_empty_change_is_refused(self):
        result = await dispatch(
            "revise_agent_facts",
            {"workflow_id": 1, "variables": {}},
            session=None,
            organization_id=7,
            user_id=3,
        )
        assert "Nothing to change" in result["error"]

    async def test_a_non_numeric_id_is_refused(self):
        result = await dispatch(
            "revise_agent_facts",
            {"workflow_id": "one", "variables": {"a": "b"}},
            session=None,
            organization_id=7,
            user_id=3,
        )
        assert "must be the number" in result["error"]


class TestOmittingAValueDoesNotEraseIt:
    async def test_the_stored_answers_are_the_base(self):
        """ "The new number is X" must not blank the address."""
        captured = {}
        stored = {
            PROVENANCE_TEMPLATE_KEY: "t1",
            "clinic_name": "Narayani",
            "address": "near the bus stand",
        }
        built = SimpleNamespace(definition={}, missing_variables=[], name="x")

        def fake_assemble(_template, *, name, variables):
            captured.update(variables)
            return built

        with (
            patch.object(
                tools.db_client,
                "get_workflow",
                AsyncMock(return_value=_workflow(stored=stored)),
            ),
            patch.object(tools.db_client, "update_workflow", AsyncMock()),
            patch.object(tools, "get_template", lambda _t: object()),
            patch.object(tools, "assemble", fake_assemble),
            patch.object(
                tools, "ReactFlowDTO", SimpleNamespace(model_validate=lambda d: d)
            ),
            patch.object(tools, "WorkflowGraph", lambda d: None),
        ):
            await dispatch(
                "revise_agent_facts",
                {"workflow_id": 1, "variables": {"phone": "9840012345"}},
                session=None,
                organization_id=7,
                user_id=3,
            )
        assert captured["address"] == "near the bus stand"
        assert captured["phone"] == "9840012345"
        assert PROVENANCE_TEMPLATE_KEY not in captured, "not a template variable"


class TestListingIsHonestAboutWhatItCanDo:
    async def test_an_agent_without_provenance_is_marked_unrevisable(self):
        rows = [
            _workflow(1, "Old one", stored={}),
            _workflow(2, "New one", stored={PROVENANCE_TEMPLATE_KEY: "t1"}),
        ]
        with (
            patch.object(
                tools.db_client, "get_all_workflows", AsyncMock(return_value=rows)
            ),
            patch.object(
                tools, "get_template", lambda t: object() if t == "t1" else None
            ),
        ):
            result = await dispatch(
                "list_my_agents", {}, session=None, organization_id=7, user_id=3
            )
        by_id = {a["workflow_id"]: a for a in result["agents"]}
        assert by_id[1]["revisable"] is False
        assert by_id[2]["revisable"] is True
