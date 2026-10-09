"""The shelf's first life stages: small business, seniors, college students
and creators (founder's choice, 8 Oct 2026).

What must appear is asserted as well as what must not: every role is on the
shelf under its stage, every starter reaches a role on that shelf and a
helper that holds its tools, and none of it needs a phone number.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.agent_templates import get_template, life_stages, list_templates
from api.services.agent_templates._base import (
    CALLING_DIRECTIONS,
    LIFE_STAGES,
    PHONE_LINE_HREF,
    AgentTemplate,
    CallDirection,
    CallStep,
)
from api.services.workflow import home_openers

#: What the founder asked for, by stage. The receptionist is the existing
#: clinic front desk, filed under small business too.
EXPECTED: dict[str, set[str]] = {
    "small_business": {
        "money_chaser",
        "compliance_clock",
        "find_customers",
        life_stages.RECEPTIONIST,
    },
    "seniors": {"medicine_caller", "scam_shield", "phone_helper", "daily_checkin"},
    "college_students": {
        "exam_planner",
        "revision_coach",
        "doubt_desk",
        "interview_coach",
    },
    "creators": {
        "content_planner",
        "caption_hook_writer",
        "brand_deal_desk",
        "performance_digest",
    },
}

NEW = life_stages.templates()
NEW_IDS = [t.id for t in NEW]

#: A price, in any of the shapes one is written here.
PRICE = re.compile(
    r"₹|\brs\.?\s?\d|\binr\b|\$\s?\d|\bpaise\b|per month|/month|a month\b|\bfree trial\b",
    re.IGNORECASE,
)

#: The call step's tool: a call is the step that needs a line, so it is
#: answered by Automatic (``call_for_me``) rather than the role's helper.
CALL_TOOLS = {"call_for_me"}


def _text(template: AgentTemplate) -> str:
    """Everything a person or a model reads off a template."""
    return " ".join(
        [
            template.name,
            template.vertical,
            template.summary,
            *template.guardrails,
            *template.compliance_notes,
            *template.example_requests,
            *template.template_variables.values(),
            *(n.prompt for n in template.nodes),
            *((n.greeting or "") for n in template.nodes),
            *(
                (template.call_step.what, template.call_step.instead)
                if template.call_step
                else ()
            ),
        ]
    )


def _decibyl_tools() -> set[str]:
    """Every tool Decibyl can hold, with every switch on."""
    from api.services import features
    from api.services.workflow import decibyl, web_tools

    with (
        patch.object(features, "is_on", lambda *a, **k: True),
        patch.object(web_tools, "enabled", lambda *a, **k: True),
    ):
        return {schema["name"] for schema in decibyl.office_tools(1)}


class TestEveryNewRole:
    @pytest.mark.parametrize("template", NEW, ids=NEW_IDS)
    def test_it_validates_round_trip(self, template):
        again = AgentTemplate.model_validate(template.model_dump())
        assert again == template

    @pytest.mark.parametrize("template", NEW, ids=NEW_IDS)
    def test_it_needs_no_number(self, template):
        assert template.direction in (CallDirection.message, CallDirection.scheduled)
        assert template.direction not in CALLING_DIRECTIONS
        assert not template.speaks
        assert not template.stack.telephony_provider
        assert template.call_shape is None

    @pytest.mark.parametrize("template", NEW, ids=NEW_IDS)
    def test_no_text_contains_a_price(self, template):
        found = PRICE.findall(_text(template))
        assert not found, f"{template.id}: {found}"

    @pytest.mark.parametrize("template", NEW, ids=NEW_IDS)
    def test_it_asks_rather_than_invents(self, template):
        prompt = " ".join(n.prompt for n in template.nodes).lower()
        assert "ask" in prompt
        assert "never fill a gap" in prompt or "never invent" in prompt
        assert "never invent" in " ".join(template.guardrails).lower()

    @pytest.mark.parametrize("template", NEW, ids=NEW_IDS)
    def test_it_names_the_existing_tools_it_uses(self, template):
        assert template.uses, template.id
        unknown = set(template.uses) - _decibyl_tools()
        assert not unknown, (
            f"{template.id} names tools Decibyl does not have: {unknown}"
        )

    @pytest.mark.parametrize("template", NEW, ids=NEW_IDS)
    def test_a_call_is_a_step_that_needs_a_line(self, template):
        """A role that rings somebody says so, says it needs a phone line,
        and names the way past it -- the care pattern."""
        prompt = " ".join(n.prompt for n in template.nodes)
        if template.call_step is None:
            assert "call_for_me" not in template.uses
            return
        assert "needs a phone line" in prompt
        assert "Settings under Phone number" in prompt
        assert template.call_step.instead in prompt
        card = template.call_step.as_card()
        assert card["needs"] == "a phone line"
        assert card["href"] == PHONE_LINE_HREF


class TestTheShelfIsGroupedByLifeStage:
    def test_each_role_is_on_the_shelf_under_its_stage(self):
        shelf = {t.id: t for t in list_templates()}
        for stage, ids in EXPECTED.items():
            for template_id in ids:
                assert template_id in shelf, template_id
                assert shelf[template_id].life_stage == stage, template_id

    def test_each_stage_holds_exactly_what_was_chosen(self):
        on_shelf: dict[str, set[str]] = {}
        for t in list_templates():
            if t.life_stage:
                on_shelf.setdefault(t.life_stage, set()).add(t.id)
        assert on_shelf == EXPECTED

    def test_the_stages_come_in_the_founders_order(self):
        assert list(LIFE_STAGES) == [
            "small_business",
            "seniors",
            "college_students",
            "creators",
        ]
        first_seen: list[str] = []
        for t in life_stages.templates():
            if t.life_stage not in first_seen:
                first_seen.append(t.life_stage)
        assert first_seen == list(LIFE_STAGES)

    def test_the_card_carries_the_stage_and_the_call_step(self):
        from api.routes.agent_templates import _summary

        card = _summary(get_template("money_chaser"))
        assert card["life_stage"] == "small_business"
        assert card["life_stage_label"] == "Small business"
        assert card["call_step"]["needs"] == "a phone line"
        assert card["call_step"]["href"] == PHONE_LINE_HREF
        assert "who_owes_me" in card["uses"]

        plain = _summary(get_template("returns_desk"))
        assert plain["life_stage"] is None
        assert plain["call_step"] is None

    def test_a_stage_that_does_not_exist_is_refused(self):
        data = get_template("scam_shield").model_dump()
        data["life_stage"] = "toddlers"
        with pytest.raises(ValueError, match="life stage"):
            AgentTemplate.model_validate(data)

    def test_a_phone_agent_cannot_also_declare_a_call_step(self):
        data = get_template("clinic_appointment").model_dump()
        data["call_step"] = CallStep(what="a call", instead="a message").model_dump()
        with pytest.raises(ValueError, match="call_step"):
            AgentTemplate.model_validate(data)


class TestTheStageRules:
    def test_students_get_tele_manas_and_no_rank(self):
        for template_id in EXPECTED["college_students"]:
            joined = " ".join(get_template(template_id).guardrails)
            assert "14416" in joined, template_id
            assert "never predict a rank" in joined.lower(), template_id
            assert "18 and over" in joined, template_id

    def test_seniors_get_no_dosing_and_no_otp(self):
        for template_id in EXPECTED["seniors"]:
            joined = " ".join(get_template(template_id).guardrails).lower()
            assert "never give advice about a medicine" in joined, template_id
            assert "never ask for an otp" in joined, template_id
            assert "plain words" in joined, template_id

    def test_the_scam_signs_are_the_care_checks_own(self):
        from api.services.care import scam

        prompt = get_template("scam_shield").start_node.prompt
        for sign in scam.SIGNS:
            assert sign.why in prompt
        assert scam.NEVER_ASKS in prompt

    def test_the_phone_steps_are_the_care_guides_own(self):
        from api.services.care import guides

        prompt = get_template("phone_helper").start_node.prompt
        for guide in guides.GUIDES:
            assert guide.title in prompt

    def test_creators_never_claim_numbers_they_were_not_given(self):
        for template_id in EXPECTED["creators"]:
            joined = " ".join(get_template(template_id).guardrails).lower()
            assert "never state a number" in joined, template_id

    def test_a_send_is_a_card(self):
        for template_id in ("money_chaser", "find_customers", "brand_deal_desk"):
            assert get_template(template_id).approve_sends, template_id


class TestImages:
    def test_off_it_writes_a_brief_and_names_no_image_tool(self):
        with patch.object(life_stages, "images_on", lambda: False):
            for t in (life_stages._content_planner(), life_stages._caption_writer()):
                prompt = t.start_node.prompt
                assert "image tool" not in prompt
                assert "brief" in prompt

    def test_on_it_may_make_one(self):
        with patch.object(life_stages, "images_on", lambda: True):
            prompt = life_stages._caption_writer().start_node.prompt
        assert "image tool" in prompt

    def test_it_reads_off_until_the_flag_exists(self):
        from api.services import features

        if life_stages._image_feature() is None:
            assert life_stages.images_on() is False
        else:
            name = life_stages._image_feature()
            assert features.FLAGS[name] == life_stages.IMAGE_GENERATION_CONSTANT


class TestStarters:
    def test_every_stage_has_four(self):
        assert set(home_openers.LIFE_STAGE_STARTERS) == set(LIFE_STAGES)
        for stage, starters in home_openers.LIFE_STAGE_STARTERS.items():
            assert len(starters) == home_openers.MAX_OPENERS, stage

    @pytest.mark.parametrize(
        "stage,starter",
        [
            (stage, starter)
            for stage, starters in home_openers.LIFE_STAGE_STARTERS.items()
            for starter in starters
        ],
        ids=lambda v: v if isinstance(v, str) else v.template,
    )
    def test_it_opens_the_right_role_and_helper(self, stage, starter):
        from api.services.helpers import catalogue

        template = get_template(starter.template)
        assert template is not None, starter.template
        assert template.life_stage == stage
        if starter.helper is None:
            # Automatic holds every tool.
            assert set(template.uses) <= _decibyl_tools()
            return
        helper = catalogue.BY_KEY.get(starter.helper)
        assert helper is not None, starter.helper
        if template.uses:
            missing = set(template.uses) - CALL_TOOLS - helper.tools
            assert not missing, f"{helper.key} cannot do {template.id}: {missing}"

    def test_every_new_role_has_a_starter(self):
        started = {
            s.template
            for starters in home_openers.LIFE_STAGE_STARTERS.values()
            for s in starters
        }
        assert set(NEW_IDS) <= started

    def test_a_role_at_the_door_decides_the_stage(self):
        assert home_openers.life_stage_for("student", "") == "college_students"
        assert home_openers.life_stage_for("senior", None) == "seniors"
        assert home_openers.life_stage_for("creator", "other") == "creators"
        assert home_openers.life_stage_for("owner", "services") == "small_business"
        assert home_openers.life_stage_for("owner", None) == "small_business"
        # A clinic owner keeps the clinic's own first jobs.
        assert home_openers.life_stage_for("owner", "clinic") is None
        assert home_openers.life_stage_for("developer", "software") is None
        assert home_openers.life_stage_for(None, None) is None

    def test_a_new_student_gets_the_students_starters(self):
        cards = home_openers.build(
            members=[], recent_questions=[], role="student", business="other"
        )
        assert [c["text"] for c in cards] == [
            s.text for s in home_openers.LIFE_STAGE_STARTERS["college_students"]
        ]
        assert all(c["kind"] == home_openers.LIFE_STAGE_KIND for c in cards)
        assert cards[0]["template"] == "exam_planner"
        assert cards[0]["helper"] == "learning_guide"

    def test_a_clinic_owner_still_gets_the_clinic_jobs(self):
        cards = home_openers.build(
            members=[], recent_questions=[], role="owner", business="clinic"
        )
        assert cards[0]["text"] == "Answer my clinic's phone and book appointments"
        assert "template" not in cards[0]

    def test_an_account_with_agents_is_unchanged(self):
        cards = home_openers.build(
            members=[{"workflow_id": 1, "name": "Desk", "calls": 3}],
            recent_questions=[],
            role="student",
        )
        assert all(c["kind"] != home_openers.LIFE_STAGE_KIND for c in cards)

    def test_the_opener_and_the_chip_carry_the_helper(self):
        from api.routes.agent_timeline import ThreadChip
        from api.routes.team import Opener

        opener = Opener(
            kind="life_stage",
            text="Give me a mock interview",
            helper="learning_guide",
            helper_name="Learning Guide",
            template="interview_coach",
        )
        assert opener.helper == "learning_guide"
        assert ThreadChip(kind="life_stage", text="x", helper="follow_up").helper


@pytest.mark.asyncio
class TestRoutingToHelpers:
    CARDS = (
        {
            "kind": "life_stage",
            "text": "a",
            "helper": "learning_guide",
            "template": "exam_planner",
        },
        {"kind": "life_stage", "text": "b", "template": "scam_shield"},
    )

    async def test_helpers_off_sends_to_automatic(self):
        with patch("api.services.helpers.states.enabled", return_value=False):
            cards = await home_openers.route_helpers(7, [dict(c) for c in self.CARDS])
        assert "helper" not in cards[0]
        assert cards[0]["template"] == "exam_planner"
        assert cards[1] == self.CARDS[1]

    async def test_an_available_helper_is_kept_with_its_name(self):
        from api.services.helpers import states

        with (
            patch.object(states, "enabled", return_value=True),
            patch.object(states, "read", new=AsyncMock(return_value=states.Readings())),
        ):
            cards = await home_openers.route_helpers(7, [dict(c) for c in self.CARDS])
        # The Learning Guide needs nothing connected.
        assert cards[0]["helper"] == "learning_guide"
        assert cards[0]["helper_name"] == "Learning Guide"

    async def test_a_helper_that_needs_setup_is_dropped_not_the_card(self):
        from api.services.helpers import states

        cards = [{"kind": "life_stage", "text": "x", "helper": "call_appointment"}]
        with (
            patch.object(states, "enabled", return_value=True),
            patch.object(
                states,
                "read",
                new=AsyncMock(
                    return_value=states.Readings(toolkits=set(), has_number=False)
                ),
            ),
        ):
            out = await home_openers.route_helpers(7, cards)
        assert out == [{"kind": "life_stage", "text": "x"}]

    async def test_a_read_that_fails_sends_to_automatic(self):
        from api.services.helpers import states

        with (
            patch.object(states, "enabled", return_value=True),
            patch.object(states, "read", new=AsyncMock(side_effect=RuntimeError("db"))),
        ):
            cards = await home_openers.route_helpers(7, [dict(c) for c in self.CARDS])
        assert "helper" not in cards[0]

    async def test_a_new_creator_reads_the_door(self):
        with (
            patch(
                "api.services.workflow.home_openers.db_client.subject_facts",
                new=AsyncMock(
                    return_value=[
                        SimpleNamespace(key="role", value="creator"),
                        SimpleNamespace(key="business", value="other"),
                    ]
                ),
            ),
            patch("api.services.helpers.states.enabled", return_value=False),
        ):
            cards = await home_openers.gather(7, members=[], unreturned_missed_calls=0)
        assert cards[0]["text"] == "Plan my content for this week"
        assert cards[0]["template"] == "content_planner"


class TestDailyCheckInIsOnTheShelf:
    def test_its_pack_is_listed_with_no_number(self):
        from api.services.packs import get_pack
        from api.services.packs.derive import is_calling

        pack = get_pack("daily_checkin")
        assert pack is not None
        assert pack.listed
        assert not is_calling(pack)
        assert pack.demo_number is None and pack.demo_url is None
        assert pack.template.life_stage == "seniors"

    def test_its_draft_is_gone(self):
        from pathlib import Path

        drafts = Path(__file__).resolve().parents[2] / "packs" / "drafts"
        assert not (drafts / "daily_wellness_checkin").exists()
