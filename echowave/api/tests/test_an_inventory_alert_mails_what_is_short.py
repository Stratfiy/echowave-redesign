"""An inventory alert reads the stock sheet and mails one person what is short.

Asked for by a procurement team on 23 Oct 2026: connect the mail, watch the
inventory, and when an item crosses its threshold mail the person what is
lacking, with the details. One threshold for every item now; the team will
set their own per item later.

Real: the template, its pack, the hire's configurations and tools from both
doors (the shelf and Chat), and the schedule. Stubbed: the workspace's
connected-app list, so the tools the hire attaches can be seen.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from api.services.agent_templates import equip, inventory
from api.services.agent_templates._base import CallDirection
from api.services.agent_templates.catalogue import get_template, list_templates
from api.services.workflow import schedule_from_words, unattended

ANSWERS = {
    "business_name": "Shree Ganesh Hardware",
    "inventory_sheet": "Inventory 2026, Stock tab",
    "default_threshold": "10",
    "alert_to": "Priya Sharma, priya@example.com",
}


def _template():
    return get_template("inventory_alert")


def _prompt(template=None):
    return (template or _template()).nodes[0].prompt


class TestTheJob:
    def test_it_runs_every_morning_without_a_number(self):
        t = _template()
        assert t.direction == CallDirection.scheduled
        assert not t.speaks
        parsed = schedule_from_words.parse(t.schedule_shape.runs)
        assert parsed is not None and parsed.at_minute == 9 * 60
        assert t.available
        assert "inventory_alert" in {x.id for x in list_templates()}

    def test_it_reads_the_sheet_and_mails_with_gmail(self):
        t = _template()
        assert t.apps == ["googlesheets", "gmail"]
        assert "never change" in _prompt()
        assert "Read the sheet; never write to it" in " ".join(t.guardrails)

    def test_one_threshold_now_and_each_items_own_later(self):
        prompt = _prompt()
        assert "own reorder level when that cell has a number" in prompt
        assert "{{default_threshold}} when the cell is blank" in prompt
        assert "at or below its threshold" in prompt

    def test_the_mail_says_what_is_lacking_with_the_details(self):
        prompt = _prompt()
        for detail in ("in stock", "threshold", "short by", "supplier", "lead time"):
            assert detail in prompt
        assert "If nothing is short, send no email." in prompt
        assert "could not read" in prompt

    def test_it_only_ever_mails_the_one_person(self):
        rules = " ".join(_template().guardrails)
        assert "Email only {{alert_to}}" in rules
        assert "Never email a supplier" in rules

    def test_every_variable_in_the_prompt_is_one_it_asks_for(self):
        t = _template()
        used = set()
        for node in t.nodes:
            used |= set(re.findall(r"{{(\w+)}}", node.prompt or ""))
        for rule in t.guardrails:
            used |= set(re.findall(r"{{(\w+)}}", rule))
        assert used <= set(t.template_variables)


class TestItMaySendOnItsOwnRuns:
    def test_the_hire_turns_unattended_sends_on(self):
        configurations = equip.configurations(None, template=_template())
        assert unattended.writes_allowed(configurations)
        assert configurations["channel"] == "chat"

    def test_a_role_that_does_not_ask_for_it_stays_read_only(self):
        for slug in ("report_generator", "marketing_manager"):
            assert not unattended.writes_allowed(
                equip.configurations(None, template=get_template(slug))
            )


class TestThePack:
    def test_it_asks_what_the_prompt_needs_and_needs_both_apps(self):
        from api.services.packs.catalogue import all_packs

        pack = next(p for p in all_packs() if p.slug == "inventory_alert")
        assert pack.listed
        assert set(_template().template_variables) == {
            f.key for f in pack.required_facts
        }
        assert {c.app for c in pack.required_connectors} == {"googlesheets", "gmail"}
        assert all(c.required for c in pack.required_connectors)


SHEETS = SimpleNamespace(
    tool_uuid="tool-sheets",
    name="Google Sheets",
    definition={"toolkit": "googlesheets"},
)


@pytest.mark.asyncio
class TestHiredFromChat:
    """Decibyl's door (``create_bot`` -> ``_create_agent``), which put no
    tools and no settings on an agent made from a template."""

    async def test_it_gets_its_apps_its_settings_and_its_schedule(self):
        from api.services.agent_builder import tools as builder_tools

        create = AsyncMock(return_value=SimpleNamespace(id=77))
        update = AsyncMock()
        routine = AsyncMock()
        for_template = AsyncMock(side_effect=lambda d, **_: {**d, "equipped": True})
        with (
            patch.object(builder_tools.db_client, "create_workflow", create),
            patch.object(builder_tools.db_client, "update_workflow", update),
            patch.object(builder_tools.db_client, "create_routine", routine),
            patch.object(equip, "for_template", for_template),
        ):
            result = await builder_tools._create_agent(
                organization_id=1,
                user_id=2,
                template_id="inventory_alert",
                name="Inventory alert",
                variables=ANSWERS,
            )
        assert result["created"] and result["schedule"] == "every day at 09:00"
        assert for_template.await_args.kwargs["organization_id"] == 1
        assert create.await_args.kwargs["workflow_definition"]["equipped"] is True
        configurations = update.await_args.kwargs["workflow_configurations"]
        assert unattended.writes_allowed(configurations)
        assert configurations["channel"] == "chat"
        assert routine.await_args.kwargs["at_minute"] == 9 * 60

    async def test_an_approval_role_keeps_its_cards_from_chat_too(self):
        from api.services.agent_builder import tools as builder_tools
        from api.services.workflow import send_approval

        update = AsyncMock()
        with (
            patch.object(
                builder_tools.db_client,
                "create_workflow",
                AsyncMock(return_value=SimpleNamespace(id=78)),
            ),
            patch.object(builder_tools.db_client, "update_workflow", update),
            patch.object(builder_tools.db_client, "create_routine", AsyncMock()),
            patch.object(
                equip, "for_template", AsyncMock(side_effect=lambda d, **_: d)
            ),
        ):
            await builder_tools._create_agent(
                organization_id=1,
                user_id=2,
                template_id="marketing_manager",
                name="Marketing",
                variables={
                    "business_name": "Narayani Sweets",
                    "what_you_sell": "Sweets",
                    "customers": "Families in Chennai",
                    "channels": "Instagram",
                    "post_language": "Tamil",
                },
            )
        configurations = update.await_args.kwargs["workflow_configurations"]
        assert send_approval.wants_approval(configurations)


@pytest.fixture
async def people(test_engine, monkeypatch):
    from api.tests.support.voice import all_on, clean, make_people

    all_on(monkeypatch)
    p = await make_people(f"inv-{uuid4().hex[:6]}")
    yield p
    await clean(p)


@pytest.mark.asyncio
async def test_hired_from_the_shelf_it_is_live_scheduled_and_may_send(people):
    from api.db import db_client
    from api.routes.agent_templates import (
        CreateFromTemplateRequest,
        create_from_template,
    )

    made = await create_from_template(
        "inventory_alert",
        CreateFromTemplateRequest(variables=ANSWERS),
        user=people.as_a,
    )
    assert made["is_live"] and made["unanswered"] == []
    assert made["schedule"] == "every day at 09:00"
    workflow = await db_client.get_workflow(made["id"], organization_id=people.org)
    assert unattended.writes_allowed(workflow.workflow_configurations)
    prompt = next(
        n["data"]["prompt"]
        for n in workflow.workflow_definition["nodes"]
        if n["type"] == "startCall"
    )
    assert "Inventory 2026, Stock tab" in prompt
    assert "priya@example.com" in prompt
    assert "{{" not in prompt.replace("{{business_name}}", "")
    [routine] = await db_client.routines_for_workflow(
        made["id"], organization_id=people.org
    )
    assert not routine.is_active


def test_the_module_says_why():
    assert inventory.RUNS == "every day at 9am"
