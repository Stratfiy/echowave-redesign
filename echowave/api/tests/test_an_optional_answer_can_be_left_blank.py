"""An answer a template marks optional can be left blank, and a proposal
turned back for a missing answer leaves Decibyl able to ask for it.

Seen on 6 October 2026: asked to create the outreach agent, the person said
"Booking link: leave it blank for now". booking_link was optional in the
template's own words but required by the build, so create_bot came back
"ask the person for these first: booking_link"; that refusal took Decibyl's
tools away and the thread read "I have nothing to add on that."
"""

from __future__ import annotations

from types import SimpleNamespace

from api.services.agent_builder.assemble import assemble, required_variables
from api.services.agent_templates import get_template
from api.services.workflow import decibyl

ANSWERS = {
    "who_we_are": "Netoyed: managed SOC and cloud for banks and NBFCs",
    "ideal_customer": "Banks and NBFCs in India with 200+ employees",
    "offer": "A free 2-week security and cloud assessment",
    "sender_name": "Nithish Kalyan, Netoyed",
    "per_run": "10",
}


def test_the_outreach_agent_builds_without_a_booking_link_or_follow_up_gap():
    template = get_template("outbound_prospecting")
    built = assemble(template, name="Outreach", variables=ANSWERS)
    assert built.missing_variables == []
    # Filled with nothing, never left as braces; the prompts say what to do
    # when it is blank.
    prompts = " ".join(node["data"]["prompt"] for node in built.definition["nodes"])
    assert "{{booking_link}}" not in prompts
    assert "{{follow_up_days}}" not in prompts
    assert "Booking link: ." in prompts


def test_optional_answers_are_still_offered_and_used_when_given():
    template = get_template("outbound_prospecting")
    assert {"booking_link", "follow_up_days"} <= set(template.template_variables)
    assert "booking_link" not in required_variables(template)
    built = assemble(
        template,
        name="Outreach",
        variables={**ANSWERS, "booking_link": "https://cal.com/netoyed/intro"},
    )
    prompts = " ".join(node["data"]["prompt"] for node in built.definition["nodes"])
    assert "https://cal.com/netoyed/intro" in prompts


def test_every_optional_answer_is_one_the_template_declares():
    from api.services.agent_templates import list_templates

    for template in list_templates():
        assert set(template.optional_variables) <= set(template.template_variables), (
            template.id
        )


def test_a_proposal_turned_back_keeps_decibyl_s_tools():
    call = SimpleNamespace(name="propose_action")
    turned_back = {
        "status": "not_proposed",
        "reason": "Ask the person for these first, then propose again: x.",
    }
    assert decibyl._was_a_read(call, turned_back)
    # A card that was written still ends the round.
    assert not decibyl._was_a_read(call, {"status": "proposed", "id": 1})
