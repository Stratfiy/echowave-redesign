"""The four templates a new account starts from.

A template that does not validate is worse than no template: it is discovered
by a customer, in the editor, on their first day. So every one is put through
the same DTO and graph checks a hand-built agent faces, plus the rules that
matter for the job each one does.
"""

import pytest

from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.launch_templates import LAUNCH_TEMPLATES, build_all
from api.services.workflow.workflow_graph import WorkflowGraph

ALL = build_all()
IDS = [name for name, _, _ in ALL]


@pytest.mark.parametrize("name,description,definition", ALL, ids=IDS)
class TestEveryTemplate:
    def test_it_validates(self, name, description, definition):
        dto = ReactFlowDTO.model_validate(definition)
        WorkflowGraph(dto)

    def test_it_has_one_start_and_at_least_one_end(self, name, description, definition):
        types = [n["type"] for n in definition["nodes"]]
        assert types.count("startCall") == 1
        assert types.count("endCall") >= 1

    def test_every_edge_connects_real_nodes(self, name, description, definition):
        ids = {n["id"] for n in definition["nodes"]}
        for edge in definition["edges"]:
            assert edge["source"] in ids, f"{name}: dangling source {edge['source']}"
            assert edge["target"] in ids, f"{name}: dangling target {edge['target']}"

    def test_it_captures_an_outcome(self, name, description, definition):
        # The owner reads the outcome, never the transcript. A template that
        # gathers nothing leaves them with a call list and no answers.
        variables = [
            v["name"]
            for n in definition["nodes"]
            for v in n["data"].get("extraction_variables", [])
        ]
        assert "call_outcome" in variables, f"{name} captures no call_outcome"

    def test_the_persona_names_no_language(self, name, description, definition):
        persona = next(n for n in definition["nodes"] if n["type"] == "globalNode")[
            "data"
        ]["prompt"].lower()
        for language in ("telugu", "hindi", "tamil", "kannada", "marathi"):
            assert language not in persona

    def test_prompts_are_not_empty(self, name, description, definition):
        for node in definition["nodes"]:
            prompt = node["data"].get("prompt")
            if prompt is not None:
                assert prompt.strip(), f"{name}: {node['id']} has a blank prompt"


class TestTheSet:
    def test_there_are_four(self):
        assert len(LAUNCH_TEMPLATES) == 4

    def test_names_are_unique(self):
        names = [name for name, _, _ in LAUNCH_TEMPLATES]
        assert len(set(names)) == len(names)

    def test_descriptions_name_the_money_not_the_mechanism(self):
        # An owner picks from these. "Branch node with rule evaluation" is not
        # a thing anybody wants; "chases quotes that went quiet" is.
        for _, description, _ in LAUNCH_TEMPLATES:
            lowered = description.lower()
            for jargon in ("node", "workflow", "llm", "prompt", "edge"):
                assert jargon not in lowered, f"{description!r} leaks {jargon!r}"


class TestPaymentRecoverySafety:
    """The one template that can cause real harm if it misbehaves."""

    def _definition(self):
        return next(d for n, _, d in ALL if "payment" in n.lower())

    def test_it_is_told_never_to_take_card_details(self):
        prompts = " ".join(
            n["data"].get("prompt", "") for n in self._definition()["nodes"]
        ).lower()
        assert "never" in prompts
        for secret in ("cvv", "otp", "pin"):
            assert secret in prompts, (
                f"the payment template must explicitly refuse {secret}"
            )

    def test_it_sends_a_link_rather_than_collecting_payment(self):
        prompts = " ".join(
            n["data"].get("prompt", "") for n in self._definition()["nodes"]
        ).lower()
        assert "link" in prompts
