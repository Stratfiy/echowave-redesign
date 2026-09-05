"""Every way of creating an agent creates one that reviews its calls.

QA scoring has worked for a long time and ran on almost nothing, because
``run_integrations`` looks for a node of type ``qa`` and no creation path made
one. An account built an agent, took a hundred calls, opened Analysis and found
it empty.

There are four ways an agent comes into existence — the wizard's local starter,
the launch templates, the clinic pack, and the six agent templates — and the
guarantee is only worth anything if it holds on all of them. A default that
holds on three paths is a default that a customer discovers is missing on the
fourth, which is worse than not having it: they were told their calls are
reviewed.

So the assertion is per-path and by name, and it is *exactly one* node rather
than at least one: two review nodes would run the analysis twice and bill the
customer for both.
"""

from api.services.agent_templates import list_templates
from api.services.agent_templates.materialise import to_workflow_definition
from api.services.workflow.launch_templates import build_all
from api.services.workflow.qa_node import QA_NODE_TYPE
from api.services.workflow.template_generation import build_starter_workflow


def _review_nodes(definition):
    return [n for n in definition["nodes"] if n.get("type") == QA_NODE_TYPE]


def _assert_reviews_calls(definition, path: str):
    nodes = _review_nodes(definition)
    assert len(nodes) == 1, f"{path} produced {len(nodes)} review nodes, want 1"
    assert nodes[0]["data"]["qa_enabled"] is True, f"{path} ships review switched off"


class TestEveryCreationPathReviewsCalls:
    def test_the_wizards_starter_workflow(self):
        for call_type in ("INBOUND", "OUTBOUND"):
            data = build_starter_workflow(call_type, "Support", "Help the caller.")
            _assert_reviews_calls(data["workflow_definition"], f"wizard/{call_type}")

    def test_every_launch_template(self):
        # Covers the clinic pack too: ``build_all`` returns both, and the pack
        # builds its definitions in its own module, which is exactly how a
        # default like this goes stale on one path and not the others.
        built = build_all()
        assert built
        for name, _description, definition in built:
            _assert_reviews_calls(definition, f"launch template/{name}")

    def test_every_agent_template(self):
        templates = list_templates()
        assert templates
        for template in templates:
            _assert_reviews_calls(
                to_workflow_definition(template), f"agent template/{template.id}"
            )


class TestTheReviewNodeIsNotAConversationStep:
    """No edges touch it, on any path.

    ``run_integrations`` collects review nodes by type once the call has ended;
    it never walks to one during a call. An edge into this node would put it in
    the conversation's path, where the caller can end up sitting at a step that
    has nothing to say.
    """

    def test_no_edge_references_it(self):
        definitions = [
            build_starter_workflow("INBOUND", "Support", "Help them.")[
                "workflow_definition"
            ],
            *(definition for _n, _d, definition in build_all()),
            *(to_workflow_definition(t) for t in list_templates()),
        ]
        for definition in definitions:
            review_ids = {n["id"] for n in _review_nodes(definition)}
            for edge in definition["edges"]:
                assert edge["source"] not in review_ids
                assert edge["target"] not in review_ids
