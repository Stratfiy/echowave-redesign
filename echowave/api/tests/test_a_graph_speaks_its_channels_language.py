"""A non-voice agent's graph does not say "Start Call".

Seen on 22 September 2026 on the pilot's own Outbound Prospecting agent, a
scheduled email agent: its canvas read Start Call -> Draft one email each ->
Close (End Call), with a "Call review" node beside it and a Test button that
offered to dial. Three causes, none of them the node types:

- the hire never wrote ``channel``, and an absent channel reads as voice
  (``workflow_configurations.channel_of``), so the editor believed it;
- ``materialise`` built the start node without the template's own name, so
  "Find prospects" came out as the spec default "Start Call";
- the review node is named "Call review" on every creation path.

The brief builder (``template_generation``) already got this right for a
chat bot: "Start" / "End", no interruption. The hire now does the same, and
says which channel it is so the editor can too.
"""

from __future__ import annotations

from api.enums import BotChannel
from api.schemas.workflow_configurations import channel_of
from api.services.agent_templates import equip
from api.services.agent_templates.catalogue import get_template, list_templates
from api.services.agent_templates.materialise import to_workflow_definition
from api.services.workflow.launch_templates import _start
from api.services.workflow.qa_node import QA_NODE_ID, qa_node


def _by_type(definition, node_type):
    return [n for n in definition["nodes"] if n["type"] == node_type]


class TestTheHireSaysWhichChannel:
    def test_a_scheduled_template_is_a_chat_bot(self):
        t = get_template("outbound_prospecting")
        assert t is not None and not t.speaks
        configurations = equip.configurations(None, template=t)
        assert channel_of(configurations) is BotChannel.CHAT

    def test_a_calling_template_says_so_rather_than_leaving_it_blank(self):
        # Explicit, even though absent would read the same: "absent means
        # voice" is a rule about old rows, not a way to write new ones.
        t = next(t for t in list_templates() if t.speaks)
        configurations = equip.configurations(None, template=t)
        assert configurations["channel"] == BotChannel.VOICE.value

    def test_the_channel_does_not_overwrite_what_the_hire_already_chose(self):
        t = get_template("outbound_prospecting")
        assert equip.configurations({"x": 1}, template=t)["x"] == 1


class TestTheGraphKeepsItsOwnWords:
    def test_the_start_node_keeps_the_templates_name(self):
        # "Find prospects", not the spec default. Every template names its
        # start node, and the hire was throwing the name away.
        for t in list_templates():
            start = _by_type(to_workflow_definition(t), "startCall")[0]
            own = next(n for n in t.nodes if n.type == "startCall")
            assert start["data"]["name"] == own.name, t.id

    def test_a_chat_agent_is_not_told_it_can_be_interrupted(self):
        # Interruption is a fact about speech; a message is sent whole.
        t = get_template("outbound_prospecting")
        start = _by_type(to_workflow_definition(t), "startCall")[0]
        assert start["data"]["allow_interrupt"] is False

    def test_a_calling_agent_still_is(self):
        t = next(t for t in list_templates() if t.speaks)
        start = _by_type(to_workflow_definition(t), "startCall")[0]
        assert start["data"]["allow_interrupt"] is True

    def test_review_is_not_called_call_review_off_the_phone(self):
        t = get_template("outbound_prospecting")
        review = next(
            n for n in to_workflow_definition(t)["nodes"] if n["id"] == QA_NODE_ID
        )
        assert review["data"]["name"] == "Review"
        assert review["data"]["qa_enabled"] is True

    def test_review_keeps_its_name_on_the_phone(self):
        t = next(t for t in list_templates() if t.speaks)
        review = next(
            n for n in to_workflow_definition(t)["nodes"] if n["id"] == QA_NODE_ID
        )
        assert review["data"]["name"] == "Call review"


class TestNothingElseMoved:
    def test_the_launch_template_builders_default_as_before(self):
        # The launch templates and the wizard call these without the new
        # arguments and must get exactly what they always got.
        assert _start("hi", "p")["data"]["name"] == "Start Call"
        assert _start("hi", "p")["data"]["allow_interrupt"] is True
        assert qa_node()["data"]["name"] == "Call review"
