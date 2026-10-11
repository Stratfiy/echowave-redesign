"""A marketing manager plans and writes the week's posts every Monday.

Real: the template, its pack, the hire's configurations, the schedule its
words parse to, the image gate both ways, and the routine runner's
deliverable. Stubbed: the run's database rows, the model's turns and the
timeline write, each checked for what it was handed.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.services.agent_templates import equip, marketing
from api.services.agent_templates._base import CallDirection
from api.services.agent_templates.catalogue import get_template, list_templates
from api.services.images import tools as image_tools
from api.services.packs._base import CALLING_CHANNELS
from api.services.workflow import one_shot_run, routine_runner, schedule_from_words


@pytest.fixture
def images_off(monkeypatch):
    monkeypatch.setattr(constants, "IMAGE_GENERATION_ENABLED", False)


@pytest.fixture
def images_on(monkeypatch):
    monkeypatch.setattr(constants, "IMAGE_GENERATION_ENABLED", True)


def _template():
    return get_template("marketing_manager")


class TestTheRole:
    def test_it_runs_on_the_clock_and_writes_to_the_owner(self):
        t = _template()
        assert t.direction == CallDirection.scheduled
        assert not t.speaks
        assert t.needs_web and t.approve_sends
        assert {"googlesheets", "gmail", "mailchimp"} <= set(t.apps)

    def test_every_monday_morning_is_a_schedule_the_hire_can_create(self):
        parsed = schedule_from_words.parse(_template().schedule_shape.runs)
        assert parsed is not None
        assert parsed.weekday == 0 and parsed.at_minute == 9 * 60 + 30

    def test_every_variable_in_the_prompt_is_one_it_asks_for(self):
        t = _template()
        used = set()
        for node in t.nodes:
            used |= set(re.findall(r"{{(\w+)}}", node.prompt or ""))
        assert used <= set(t.template_variables)
        assert set(t.optional_variables) <= set(t.template_variables)

    def test_it_never_makes_up_an_offer_or_posts_by_itself(self):
        rules = " ".join(_template().guardrails)
        assert "Never invent a price" in rules
        assert "[your offer]" in rules
        assert "Never post, publish or send anything yourself" in rules
        assert "how to stop" in rules

    def test_the_plan_is_the_runs_last_message(self):
        prompt = _template().nodes[0].prompt
        assert "Your last message is the plan, in full" in prompt
        assert "caption in full" in prompt and "hashtags" in prompt
        assert "promotion message" in prompt


class TestTheOccasions:
    def test_the_big_ones_are_on_the_list(self):
        names = " ".join(name for _, name in marketing.OCCASIONS)
        for name in ("Diwali", "Holi", "Republic Day", "Independence Day", "Eid"):
            assert name in names

    def test_fixed_dates_are_real_days_and_in_order(self):
        seen = []
        for when, _ in marketing.OCCASIONS:
            if when.startswith("around"):
                continue
            seen.append(datetime.strptime(when, "%d %b %Y").date())
        assert seen == sorted(seen)
        assert date(2026, 11, 8) in seen  # Diwali 2026
        assert date(2027, 1, 26) in seen

    def test_moon_dates_are_marked_and_the_prompt_says_to_check_them(self):
        whens = dict((name, when) for when, name in marketing.OCCASIONS)
        assert whens["Holi"].startswith("around")
        assert whens["Eid al-Fitr (set by the moon sighting)"].startswith("around")
        block = marketing.occasions_block()
        assert "check it on the web" in block
        assert marketing.OCCASIONS_END in block
        assert block in _template().nodes[0].prompt


class TestImages:
    def test_on_the_gallery_whether_or_not_images_are_on(self, images_off):
        t = _template()
        assert t.requires_feature is None and t.available
        assert "marketing_manager" in {x.id for x in list_templates()}

    def test_the_hire_is_marked_so_posters_come_once_images_are_on(self):
        configurations = equip.configurations(None, template=_template())
        assert image_tools.wants_images(configurations)

    def test_without_the_tool_it_writes_the_brief(self, images_off):
        prompt = _template().nodes[0].prompt
        assert "If make_images is not in this conversation" in prompt
        assert "brief" in prompt

    def test_the_creator_roles_are_marked_too(self):
        for slug in ("content_planner", "caption_hook_writer"):
            assert image_tools.wants_images(
                equip.configurations(None, template=get_template(slug))
            )

    def test_a_role_that_neither_needs_nor_offers_images_is_left_alone(self):
        assert not image_tools.wants_images(
            equip.configurations(None, template=get_template("report_generator"))
        )


class TestThePack:
    def test_it_is_on_the_shelf_with_the_questions_the_prompt_needs(self, images_off):
        from api.services.packs.catalogue import all_packs

        pack = next(p for p in all_packs() if p.slug == "marketing_manager")
        assert pack.listed
        assert not set(pack.channels) & CALLING_CHANNELS
        keys = {fact.key for fact in pack.required_facts}
        assert set(_template().template_variables) <= keys
        optional = {f.key for f in pack.required_facts if not f.required}
        assert optional == {"plan_sheet"}
        assert all(not c.required for c in pack.required_connectors)


@pytest.mark.asyncio
async def test_the_whole_plan_reaches_the_thread_not_its_first_500_characters():
    plan = "Theme: Diwali week." + "\nMon 7pm Instagram: caption..." * 200
    assert len(plan) > 5_000
    session = SimpleNamespace(
        session_data={"turns": [{"assistant_message": {"text": plan}}]},
        revision=1,
    )
    record = AsyncMock()
    with (
        patch.object(
            routine_runner,
            "_load",
            AsyncMock(
                return_value={
                    "id": 1,
                    "organization_id": 7,
                    "workflow_id": 3,
                    "name": "Marketing manager",
                    "instruction": "Start today's run, as your steps describe.",
                }
            ),
        ),
        patch.object(
            routine_runner.db_client,
            "create_workflow_run",
            AsyncMock(return_value=SimpleNamespace(id=41)),
        ),
        patch.object(
            routine_runner,
            "authorize_workflow_run_start",
            AsyncMock(return_value=SimpleNamespace(has_quota=True)),
        ),
        patch.object(routine_runner.db_client, "update_workflow_run", AsyncMock()),
        patch.object(
            routine_runner.db_client,
            "ensure_workflow_run_text_session",
            AsyncMock(return_value=session),
        ),
        patch.object(
            routine_runner.db_client,
            "get_workflow_by_id",
            AsyncMock(return_value=SimpleNamespace(workflow_configurations={})),
        ),
        patch.object(
            routine_runner,
            "initialize_text_chat_session",
            AsyncMock(return_value=session),
        ),
        patch.object(
            routine_runner,
            "execute_pending_text_chat_turn",
            AsyncMock(return_value=session),
        ),
        patch.object(
            routine_runner,
            "append_text_chat_user_message",
            AsyncMock(return_value=session),
        ),
        patch.object(
            routine_runner, "_first_routine_run_is_free", AsyncMock(return_value=True)
        ),
        patch.object(routine_runner.agent_timeline, "record", record),
        patch.object(one_shot_run, "close", AsyncMock()),
    ):
        assert await routine_runner.run_routine(1) == 41
    kwargs = record.await_args.kwargs
    assert kwargs["kind"] == "deliverable"
    assert kwargs["payload"]["body"] == plan
    assert kwargs["payload"]["routine"] == "Marketing manager"
