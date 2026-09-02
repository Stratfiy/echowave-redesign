"""The clinic pack's safety rules.

`test_launch_templates.py` already checks that every template — these included
— validates, has one start, connects its edges and captures an outcome. This
file only covers what is specific to clinics, and both of those are safety
rules rather than preferences: an agent that answers a medical question, or
that confirms an appointment nobody booked, is a different class of failure
from an agent that sounds wooden.
"""

import pytest

from api.services.workflow.clinic_pack import CLINIC_TEMPLATES

BUILT = [(name, build()) for name, _description, build in CLINIC_TEMPLATES]


def prompts(definition) -> str:
    """Every prompt and greeting in one string, lowercased."""
    parts = []
    for node in definition["nodes"]:
        data = node["data"]
        parts += [data.get("prompt", ""), data.get("greeting", "")]
    return " ".join(parts).lower()


@pytest.mark.parametrize("name,definition", BUILT, ids=[n for n, _ in BUILT])
class TestEveryClinicTemplate:
    def test_it_refuses_clinical_conversation(self, name, definition):
        """The boundary lives in the persona so it applies to every node.

        Put in one node's prompt instead, it would hold until somebody adds a
        second node — and the agent that starts answering symptom questions is
        the one that ends the company, not the one that books badly.
        """
        text = prompts(definition)
        assert "never give medical advice" in text, f"{name} has no clinical guardrail"
        assert "symptom" in text

    def test_it_offers_a_human_for_anything_clinical(self, name, definition):
        """Refusing is not enough. A caller with a medical question who is told
        "I can't help with that" and nothing else has been dropped, not
        served."""
        text = prompts(definition)
        assert "team" in text or "transfer" in text or "callback" in text

    def test_it_never_confirms_a_slot_it_cannot_see(self, name, definition):
        """There is no calendar integration. An agent that says "you're booked
        for 4pm" produces a patient standing in a waiting room with no
        appointment, and the clinic blames us in front of that patient.

        Asserted on the agent nodes specifically. Checked across the whole
        template it passes on the end node's "the desk will confirm", which is
        a closing line and not an instruction — so deleting the actual rule
        from the node that takes the booking left the test green.
        """
        agent_prompts = " ".join(
            n["data"].get("prompt", "")
            for n in definition["nodes"]
            if n["type"] == "agentNode"
        ).lower()
        assert "do not confirm" in agent_prompts, (
            f"{name}'s agent node is not told to avoid confirming a slot"
        )

    def test_the_business_name_is_a_variable(self, name, definition):
        """A rep fills six fields. A hardcoded clinic name in the greeting is
        one of them silently not working."""
        assert "{{business_name}}" in prompts(definition)


class TestTheSet:
    def test_names_are_unique_and_marked_as_a_pack(self):
        names = [name for name, _, _ in CLINIC_TEMPLATES]
        assert len(set(names)) == len(names)
        assert all(n.startswith("Clinic — ") for n in names)

    def test_they_reach_the_seeded_set(self):
        """The pack is only worth anything if it ships. build_all() is what the
        seeder installs, so this is the wiring that actually matters."""
        from api.services.workflow.launch_templates import build_all

        seeded = {name for name, _, _ in build_all()}
        for name, _, _ in CLINIC_TEMPLATES:
            assert name in seeded

    def test_the_generic_templates_are_still_there(self):
        """Adding a pack must not displace the four that serve every other
        vertical."""
        from api.services.workflow.launch_templates import build_all

        seeded = {name for name, _, _ in build_all()}
        assert "Missed call callback" in seeded
        assert "Website enquiry" in seeded
