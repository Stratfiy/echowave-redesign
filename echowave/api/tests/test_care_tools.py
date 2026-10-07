"""Care in Chat (launch stream care): Decibyl's care tools call the same
services as the care screens.

Done when: a scam check from Chat returns the same verdict and stores no
words; phone help starts and moves on from Chat; a reminder asked for in
Chat is a card, not a call; each is refused when its flag is off for the
workspace; and the reminder tool ends the round like any card while the
reads keep the model's tools.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api import constants
from api.services.care import tools
from api.services.workflow import actions, decibyl
from api.tests import care_support as cs


@pytest.fixture
async def amma(test_engine, monkeypatch):
    cs.all_on(monkeypatch)
    monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "taken")
    person = await cs.person("tools-amma")
    org = await cs.workspace(person.id)
    yield person, org
    await cs.cleanup(org)


def _call(name, **arguments):
    return SimpleNamespace(name=name, arguments=arguments, id=f"call-{name}")


@pytest.mark.asyncio
async def test_scam_check_from_chat(amma):
    person, org = amma
    result = await decibyl._tool(
        org,
        _call(tools.SCAM_TOOL, text="Tell me the OTP to stop your card block"),
        author_id=person.id,
    )
    assert result["status"] == "success" and result["verdict"] == "likely_scam"
    assert result["never_asks"].startswith("Decibyl will never ask")
    assert decibyl._was_a_read(_call(tools.SCAM_TOOL), result) is True


@pytest.mark.asyncio
async def test_phone_help_from_chat(amma):
    person, org = amma
    started = await decibyl._tool(
        org,
        _call(tools.HELP_START_TOOL, question="make the writing bigger"),
        author_id=person.id,
    )
    session = started["session"]
    nxt = await decibyl._tool(
        org,
        _call(
            tools.HELP_ANSWER_TOOL,
            session_id=session["id"],
            version=session["version"],
            worked=True,
        ),
        author_id=person.id,
    )
    assert nxt["session"]["step_number"] == 2


@pytest.mark.asyncio
async def test_a_reminder_from_chat_is_a_card_not_a_call(amma):
    person, org = amma
    result = await decibyl._tool(
        org,
        _call(
            tools.REMINDER_TOOL,
            label="Sugar tablet",
            times=["09:00"],
            phone="9876543210",
        ),
        author_id=person.id,
    )
    assert result["status"] == "proposed"
    assert decibyl._was_a_read(_call(tools.REMINDER_TOOL), result) is False
    from api.services.care import medicines

    meds = await medicines.list_mine(org, person.id)
    assert meds[0]["state"] == medicines.AWAITING
    event = await cs.db_client.get_agent_event(
        meds[0]["card_event_id"], organization_id=org
    )
    assert event.payload["action"] == actions.CARE_MEDICINE_CALLS


@pytest.mark.asyncio
async def test_off_for_the_workspace_means_no_such_tool(amma, monkeypatch):
    person, org = amma
    monkeypatch.setattr(constants, "CARE_SCAM_CHECK_ENABLED", False)
    result = await decibyl._tool(
        org, _call(tools.SCAM_TOOL, text="OTP please"), author_id=person.id
    )
    assert result == {"status": "unavailable", "reason": "no such tool"}
    assert tools.SCAM_TOOL not in decibyl.system_prompt(org)
