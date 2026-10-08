"""A chat agent built from Chat says where to talk to it.

Found on staging: after "Build me a research agent" was confirmed, the
person asked "How do I use it?" and Decibyl answered "I can't see from here
which channel or link it answers on", while the done card offered "Hear it"
(a phone test) for an agent that has no phone. The build now records the
channel, the done line says where it answers, and the card opens its chat.
"""

from __future__ import annotations

from api.services.workflow import bot_from_brief


def test_a_chat_agent_says_it_answers_in_its_own_chat():
    note = bot_from_brief.done_note("Research agent", 5, channel="chat")
    assert note.startswith("Built Research agent — 5 steps.")
    assert "Open its chat" in note


def test_a_voice_agent_says_what_it_always_said():
    assert bot_from_brief.done_note("Front desk", 1, channel="voice") == (
        "Built Front desk — 1 step."
    )
