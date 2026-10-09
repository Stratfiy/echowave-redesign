"""Changing who an agent signs as touches that name and nothing else.

Seen on 8 October 2026 with the Netoyed outreach agent, which signs its email
in three steps. From the chat, "change the sender name" became a card that
rewrote the agent's Rules into one line and deleted every rule it had: the
model is shown each step cut at 1,500 characters and was asked for "the
complete new prompt". From the editor, 'Replace "Rahul Mehta" with "Nithish
Kalyan, Netoyed" in every step' came back "could not be validated".
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.agent_builder import edit_proposal
from api.services.agent_builder.assemble import assemble
from api.services.agent_templates import get_template
from api.services.workflow import self_edit

OLD, NEW = "Rahul Mehta", "Nithish Kalyan, Netoyed"


def _outreach_graph() -> dict:
    built = assemble(
        get_template("outbound_prospecting"),
        name="Netoyed Outreach",
        variables={
            "who_we_are": "Netoyed: managed SOC and cloud for banks",
            "ideal_customer": "Banks and NBFCs in India",
            "offer": "A free 2-week security assessment",
            "sender_name": OLD,
            "per_run": "10",
        },
    )
    graph = copy.deepcopy(built.definition)
    graph.setdefault("edges", [])
    return graph


def _steps_with(graph: dict, text: str) -> set[str]:
    return {
        n["data"].get("name", n["id"])
        for n in graph["nodes"]
        if text in str(n["data"].get("prompt", ""))
    }


class TestTheEditor:
    def test_the_request_as_typed_is_read_as_a_literal_replacement(self):
        found = edit_proposal.quoted_replacement(
            f'Replace "{OLD}" with "{NEW}" in every step where it signs or '
            "mentions the sender. Change nothing else."
        )
        assert (found.find, found.replace_with) == (OLD, NEW)
        assert edit_proposal.quoted_replacement("make it friendlier") is None

    @pytest.mark.asyncio
    async def test_it_is_answered_without_a_model_and_touches_only_the_name(self):
        graph = _outreach_graph()
        before = _steps_with(graph, OLD)
        assert len(before) == 3
        rules_before = next(n for n in graph["nodes"] if n["type"] == "globalNode")
        result = await edit_proposal.propose_edit(
            model=None,  # never consulted
            graph=graph,
            message=f'Replace "{OLD}" with "{NEW}" in every step.',
        )
        assert result["status"] == "proposal"
        after = result["graph"]
        assert _steps_with(after, OLD) == set()
        assert _steps_with(after, NEW) == before
        rules_after = next(n for n in after["nodes"] if n["type"] == "globalNode")
        assert rules_after["data"]["prompt"] == rules_before["data"]["prompt"]
        for change in result["changes"]:
            assert change["after"] == change["before"].replace(OLD, NEW)

    @pytest.mark.asyncio
    async def test_a_name_that_is_not_there_says_so(self):
        with pytest.raises(edit_proposal.BuilderClientError, match="does not appear"):
            await edit_proposal.propose_edit(
                model=None,
                graph=_outreach_graph(),
                message='Replace "Priya Shah" with "Someone Else"',
            )


def _workflow(graph: dict) -> SimpleNamespace:
    return SimpleNamespace(id=7, name="Netoyed Outreach", workflow_definition=graph)


@pytest.mark.asyncio
class TestTheChat:
    async def test_find_and_replace_changes_every_step_that_signs(self):
        graph = _outreach_graph()
        saved = AsyncMock(return_value=SimpleNamespace(version_number=2))
        record = AsyncMock()
        with (
            patch.object(
                self_edit.db_client,
                "get_workflow_by_id",
                AsyncMock(return_value=_workflow(graph)),
            ),
            patch.object(self_edit.db_client, "save_workflow_draft", saved),
            patch.object(self_edit.agent_timeline, "record", record),
        ):
            result = await self_edit.propose(
                organization_id=1,
                workflow_id=7,
                workflow_run_id=None,
                arguments={"find": OLD, "replace_with": NEW, "why": "real sender"},
                on_assistant_thread=True,
            )
        assert result["status"] == "proposed"
        draft = saved.call_args.kwargs["workflow_definition"]
        assert _steps_with(draft, OLD) == set()
        assert len(_steps_with(draft, NEW)) == 3
        rules = next(n for n in draft["nodes"] if n["type"] == "globalNode")
        original = next(n for n in graph["nodes"] if n["type"] == "globalNode")
        assert rules["data"]["prompt"] == original["data"]["prompt"]
        payload = record.call_args.kwargs["payload"]
        assert payload["step"] == "3 steps" and OLD in payload["diff"]

    async def test_a_rewrite_that_drops_most_of_a_step_is_refused(self):
        graph = _outreach_graph()
        saved = AsyncMock()
        with (
            patch.object(
                self_edit.db_client,
                "get_workflow_by_id",
                AsyncMock(return_value=_workflow(graph)),
            ),
            patch.object(self_edit.db_client, "save_workflow_draft", saved),
        ):
            result = await self_edit.propose(
                organization_id=1,
                workflow_id=7,
                workflow_run_id=None,
                arguments={
                    "step": "Rules",
                    "new_prompt": f"Sign every email as {NEW}.",
                    "why": "sender name",
                },
                on_assistant_thread=True,
            )
        assert result["status"] == "not_proposed"
        assert "find and replace_with" in result["reason"]
        saved.assert_not_awaited()

    async def test_a_find_that_matches_nothing_writes_no_draft(self):
        saved = AsyncMock()
        with (
            patch.object(
                self_edit.db_client,
                "get_workflow_by_id",
                AsyncMock(return_value=_workflow(_outreach_graph())),
            ),
            patch.object(self_edit.db_client, "save_workflow_draft", saved),
        ):
            result = await self_edit.propose(
                organization_id=1,
                workflow_id=7,
                workflow_run_id=None,
                arguments={"find": "Priya Shah", "replace_with": NEW, "why": "x"},
            )
        assert result["status"] == "not_proposed"
        saved.assert_not_awaited()
