"""Hiring an agent from the chat leaves it ready to run, with nothing to type
twice and nothing pretend in it.

Seen on 8 October 2026 with the Netoyed outreach agent: created from the
chat, it had no schedule although its template says "every weekday
morning" (the person had to find Setup, Triggers, Add a routine and type
it in), it was told to "attach a phone number" though it only writes
email, and "[client's name], Netoyed" was pasted back as the sender.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.agent_builder import tools as builder_tools
from api.services.workflow import actions

ANSWERS = {
    "who_we_are": "Netoyed: managed SOC and cloud for banks and NBFCs",
    "ideal_customer": "Banks and NBFCs in India with 200+ employees",
    "offer": "A free 2-week security and cloud assessment",
    "sender_name": "Nithish Kalyan, Netoyed",
    "per_run": "10",
}


def _db():
    return (
        patch.object(
            builder_tools.db_client,
            "create_workflow",
            AsyncMock(return_value=SimpleNamespace(id=41)),
        ),
        patch.object(builder_tools.db_client, "update_workflow", AsyncMock()),
    )


@pytest.mark.asyncio
class TestTheScheduleComesWithIt:
    async def test_a_scheduled_agent_gets_its_templates_schedule(self):
        routine = AsyncMock()
        create, update = _db()
        with (
            create,
            update,
            patch.object(builder_tools.db_client, "create_routine", routine),
        ):
            result = await builder_tools._create_agent(
                organization_id=1,
                user_id=2,
                template_id="outbound_prospecting",
                name="Netoyed Outreach",
                variables=ANSWERS,
            )
        assert result["created"] and result["schedule"] == "every weekday when you open"
        kwargs = routine.call_args.kwargs
        assert kwargs["workflow_id"] == 41 and kwargs["cadence"] == "weekdays"
        # No phone, and nobody is told to buy a number for it.
        assert result["calls"] is False
        assert not any("phone number" in s for s in result["next_steps"])
        assert any("Run once" in s for s in result["next_steps"])

    async def test_an_agent_that_takes_calls_gets_no_routine(self):
        from api.services.agent_templates import list_templates
        from api.services.agent_templates._base import CALLING_DIRECTIONS

        template = next(
            t for t in list_templates() if t.direction in CALLING_DIRECTIONS
        )
        routine = AsyncMock()
        create, update = _db()
        with (
            create,
            update,
            patch.object(builder_tools.db_client, "create_routine", routine),
        ):
            result = await builder_tools._create_agent(
                organization_id=1,
                user_id=2,
                template_id=template.id,
                name="Front desk",
                variables={k: f"answer for {k}" for k in template.template_variables},
            )
        assert result["created"]
        routine.assert_not_awaited()
        assert result["schedule"] is None and result["calls"] is True

    async def test_a_schedule_that_cannot_be_saved_still_leaves_the_agent(self):
        create, update = _db()
        with (
            create,
            update,
            patch.object(
                builder_tools.db_client,
                "create_routine",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            result = await builder_tools._create_agent(
                organization_id=1,
                user_id=2,
                template_id="outbound_prospecting",
                name="Netoyed Outreach",
                variables=ANSWERS,
            )
        assert result["created"] and result["schedule"] is None


class TestNoPlaceholderIsBuiltIn:
    @pytest.mark.parametrize(
        "value",
        ["[client's name], Netoyed", "<your name>", "{{sender_name}}", "XXX", "TBD"],
    )
    def test_a_stand_in_is_caught(self, value):
        assert actions._is_a_stand_in(value)

    @pytest.mark.parametrize(
        "value",
        [
            "Nithish Kalyan, Netoyed",
            "10",
            "Banks & NBFCs (200+ staff)",
            "https://cal.com/x",
        ],
    )
    def test_a_real_answer_is_not(self, value):
        assert not actions._is_a_stand_in(value)


@pytest.mark.asyncio
async def test_the_card_is_refused_while_an_answer_is_a_placeholder():
    result = await actions.propose(
        organization_id=1,
        workflow_id=None,
        workflow_run_id=None,
        arguments={
            "action": actions.CREATE_BOT,
            "template_id": "outbound_prospecting",
            "name": "Netoyed Outreach",
            "variables": {**ANSWERS, "sender_name": "[client's name], Netoyed"},
            "why": "outreach",
        },
    )
    assert result["status"] == "not_proposed"
    assert "sender_name" in result["reason"] and "placeholder" in result["reason"]
