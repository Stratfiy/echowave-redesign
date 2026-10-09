"""Decibyl builds any kind of agent from a line, and does one-off jobs itself.

A person who asks for "an agent that chases unpaid invoices every Monday"
should get a card to read, not a request to write a spec. And a person who
asks it to draft one email should get the email, not an agent that drafts
emails.
"""

import pytest

from api.services.workflow import bot_from_brief, decibyl


def test_it_writes_the_spec_itself_for_any_agent():
    assert "Any kind of agent can be built" in decibyl.SYSTEM
    assert "write it yourself from what they said" in decibyl.SYSTEM
    assert "build_bot_from_spec" in decibyl.SYSTEM


def test_it_does_one_off_jobs_as_their_own_assistant():
    assert "You are also the person's own assistant" in decibyl.SYSTEM
    assert "rather than building an agent for it" in decibyl.SYSTEM


def test_a_short_spec_sends_the_model_back_to_write_the_steps():
    with pytest.raises(bot_from_brief.BriefError) as raised:
        bot_from_brief.resolve(
            {"name": "Invoice chaser", "channel": "chat", "spec": "chase invoices"}
        )
    message = str(raised.value)
    assert "Write out the steps" in message
    assert "Ask them only if the job itself is unclear" in message


def test_the_tool_tells_the_model_to_write_a_short_ask_out():
    assert "write the spec yourself" in bot_from_brief.DESCRIPTION
