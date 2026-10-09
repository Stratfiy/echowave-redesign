"""What the screens say Decibyl and the agents know has to be in what they read.

An audit of every "the agent knows about this" promise against the text a
model is actually sent. Each test builds a realistic account in the real
database -- an agent with a routine, a skill and confirmed memory, the asker's
own reminders, a colleague's account beside it -- composes the real context
the way a turn does, and asserts what must appear (api/AGENTS.md: test what
must appear, not only what must not).

Four things were missing or wrong before this file, each silently:

* Decibyl's context had no date or time; the agents' prompts always had.
* Neither Decibyl nor an agent was told what is scheduled, though the
  Schedules screen lists every routine with its next run.
* Decibyl's "What the business has confirmed" read every fact row,
  including ones still waiting for a yes and ones a person rejected.
* A caller the number's contact list recognised was only ever a template
  variable, so no agent whose prompt did not spell ``{{contact_name}}``
  (no shipped one does) was told who was calling.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.db.care_models import CareMedicineModel
from api.db.models import (
    AgentRoutineModel,
    OrganisationFactModel,
    OrganisationSkillModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
)
from api.db.today_models import TodayEventModel, TodayReminderModel
from api.services import acting, features
from api.services.skills import catalogue
from api.services.telephony import inbound_guard
from api.services.workflow import (
    connected_tools,
    decibyl,
    files_search,
    standing_context,
    unattended,
)
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_context_composer import caller_block
from pipecat.tests import MockLLMService

ON = {"today_reminders", "care_medicine_calls"}


@pytest.fixture
def flags(monkeypatch):
    real = features.is_on
    monkeypatch.setattr(
        features,
        "is_on",
        lambda name, organization_id=None: name in ON or real(name, organization_id),
    )


@pytest.fixture
def no_vendors(monkeypatch):
    """No embeddings vendor and no app vendor: words-only search, and the
    connected-app reading answers from the rows alone."""

    async def no_embeddings(organization_id):
        return {}

    monkeypatch.setattr(files_search, "_embeddings", no_embeddings)
    monkeypatch.setattr(connected_tools, "awaiting_setup", AsyncMock(return_value=[]))


def _skill():
    return next(iter(catalogue.all_skills().values()))


async def _account(session, slug: str):
    """An account the way it looks a week in: an agent with a morning
    routine and a skill, Decibyl's own routine, memory in every state, and
    the owner's own reminders."""
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    user = UserModel(provider_id=f"user-{slug}", selected_organization_id=org.id)
    session.add(user)
    await session.flush()
    agent = WorkflowModel(
        name=f"Front desk {slug}",
        organization_id=org.id,
        user_id=user.id,
        status="active",
    )
    session.add(agent)
    await session.flush()
    now = datetime.now(UTC)
    session.add_all(
        [
            AgentRoutineModel(
                organization_id=org.id,
                workflow_id=agent.id,
                name=f"Morning numbers {slug}",
                instruction=f"Summarise yesterday's calls for {slug} and list who to call back.",
                cadence="daily",
                anchor="clock",
                at_minute=9 * 60 + 30,
                is_active=True,
                tested_at=now - timedelta(days=1),
            ),
            AgentRoutineModel(
                organization_id=org.id,
                workflow_id=None,
                name=f"Weekly board summary {slug}",
                instruction="Summarise the board.",
                cadence="weekly",
                anchor="clock",
                at_minute=17 * 60,
                weekday=4,
                is_active=False,
            ),
            OrganisationFactModel(
                organization_id=org.id,
                subject_type="organisation",
                subject_key="self",
                key=f"opening hours {slug}",
                value="9 to 6, closed Sunday",
                kind="fact",
                status="confirmed",
            ),
            OrganisationFactModel(
                organization_id=org.id,
                subject_type="organisation",
                subject_key="self",
                key=f"parking {slug}",
                value="overheard: free parking",
                kind="fact",
                status="learned",
                times_seen=50,
            ),
            OrganisationFactModel(
                organization_id=org.id,
                subject_type="organisation",
                subject_key="self",
                key=f"delivery {slug}",
                value="same day (wrong)",
                kind="fact",
                status="rejected",
                times_seen=50,
            ),
            OrganisationFactModel(
                organization_id=org.id,
                subject_type="organisation",
                subject_key="self",
                key=f"gap {slug}",
                value="nobody could answer about EMI",
                kind="gap",
                status="learned",
            ),
            OrganisationSkillModel(
                organization_id=org.id, slug=_skill().slug, workflow_id=agent.id
            ),
            TodayReminderModel(
                organization_id=org.id,
                user_id=user.id,
                title=f"Pay the electricity bill {slug}",
                timezone="Asia/Kolkata",
                remind_at=now + timedelta(days=1),
                status="active",
            ),
            TodayEventModel(
                organization_id=org.id,
                user_id=user.id,
                title=f"Dentist {slug}",
                starts_at=now + timedelta(days=2),
                timezone="Asia/Kolkata",
                status="active",
            ),
            CareMedicineModel(
                organization_id=org.id,
                person_user_id=user.id,
                label=f"BP tablet {slug}",
                times=["08:00", "20:00"],
                timezone="Asia/Kolkata",
                language="en",
                channel="app",
                state="active",
                created_by_user_id=user.id,
            ),
        ]
    )
    await session.flush()
    return org, user, agent


def _section(context: str, heading: str) -> str:
    assert f"## {heading}\n" in context, f"no {heading!r} section"
    return context.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


async def _decibyl_context(org, user, question="what is on today?"):
    with acting.acting_as(user.id if user else None):
        return await decibyl.build_context(org.id, question)


@pytest.mark.asyncio
class TestDecibylReadsTheAccount:
    async def test_every_reading_arrives(
        self, db_session, async_session, flags, no_vendors
    ):
        org, user, agent = await _account(async_session, "a")
        await _account(async_session, "qx7neighbour")  # a neighbour that must not leak

        context = await _decibyl_context(org, user)

        # The clock, in the person's zone, with the year and the weekday.
        now = _section(context, "Now")
        local = datetime.now(UTC).astimezone(
            __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
        )
        assert str(local.year) in now and local.strftime("%A") in now

        # Every routine, whose it is, on or off, next run, and its task.
        schedules = _section(context, "Schedules")
        assert f"Morning numbers a ({agent.name})" in schedules
        assert "Every day at 09:30; on, next run" in schedules
        assert "Summarise yesterday's calls for a" in schedules
        assert "Weekly board summary a (yours, Decibyl's)" in schedules
        assert "off (saved, not switched on)" in schedules

        # Confirmed memory, and only confirmed memory.
        memory = _section(context, "What the business has confirmed")
        assert "- opening hours a: 9 to 6, closed Sunday" in memory
        assert "parking a" not in memory
        assert "delivery a" not in memory
        assert "gap a" not in memory

        # The skill installed on the agent is named.
        skills = _section(context, "Skills this account installed")
        assert (_skill().title or _skill().slug) in skills

        # The asker's own reminders, events and medicine reminders.
        mine = _section(context, "Your reminders")
        assert "Pay the electricity bill a" in mine
        assert "Dentist a" in mine
        assert "BP tablet a at 08:00, 20:00" in mine

        # Nothing of the other account's, anywhere.
        assert "qx7neighbour" not in context

    async def test_an_account_with_nothing_scheduled_says_so(
        self, db_session, async_session, flags, no_vendors
    ):
        """Emptiness is an answer here: "what is scheduled?" must read
        "nothing", not a missing heading the model fills from guesswork."""
        org = OrganizationModel(provider_id="org-empty", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        context = await _decibyl_context(org, None)
        assert _section(context, "Schedules").strip() == "Nothing scheduled."
        # Nobody signed in: no personal section at all.
        assert "## Your reminders" not in context

    async def test_another_person_s_reminders_are_not_read(
        self, db_session, async_session, flags, no_vendors
    ):
        org, user, _ = await _account(async_session, "b")
        colleague = UserModel(provider_id="user-b2", selected_organization_id=org.id)
        async_session.add(colleague)
        await async_session.flush()
        context = await _decibyl_context(org, colleague)
        assert "Pay the electricity bill b" not in context
        assert "BP tablet b" not in context


class TestDecibylRoutineVisibility:
    @pytest.mark.asyncio
    async def test_a_turn_with_nobody_signed_in_sees_only_its_own_thread_s(self):
        """A Decibyl routine run has no signed-in person. A routine set in
        somebody's private chat is named only on that chat."""
        row = SimpleNamespace(
            workflow_id=None, organization_id=1, armed_by_card_event_id=5
        )
        card = SimpleNamespace(thread_id="t-private")
        with patch(
            "api.services.workflow.routines.origin_card",
            new=AsyncMock(return_value=card),
        ):
            assert await standing_context._visible_to(row, None, "t-private")
            assert not await standing_context._visible_to(row, None, "t-other")

    @pytest.mark.asyncio
    async def test_an_agent_s_routine_is_the_workspace_s(self):
        row = SimpleNamespace(workflow_id=3, organization_id=1)
        assert await standing_context._visible_to(row, None, None)

    def test_a_schedule_that_cannot_be_read_is_listed_not_dropped(self):
        row = SimpleNamespace(
            name="Odd one",
            instruction="do it",
            cadence="fortnightly",
            anchor="clock",
            at_minute=0,
            offset_minutes=0,
            weekday=0,
            is_active=True,
            tested_at=None,
            last_fired_at=None,
            needs_apps=[],
            armed_by_card_event_id=None,
            last_skipped_reason=None,
            workflow_id=3,
        )
        block = standing_context.routines_block([row], zone_name="Asia/Kolkata")
        assert "Odd one" in block and "cannot be read" in block

    def test_a_long_list_says_how_many_were_left_out(self):
        rows = [
            SimpleNamespace(
                name=f"R{n}",
                instruction="",
                cadence="daily",
                anchor="clock",
                at_minute=60,
                offset_minutes=0,
                weekday=0,
                is_active=False,
                tested_at=None,
                last_fired_at=None,
                needs_apps=[],
                armed_by_card_event_id=None,
                last_skipped_reason=None,
                workflow_id=3,
            )
            for n in range(standing_context.MAX_ROUTINES + 3)
        ]
        block = standing_context.routines_block(rows, zone_name="Asia/Kolkata")
        assert "3 more routines not shown here" in block


def _engine(workflow, call_context_vars, *, is_voice):
    return PipecatEngine(
        llm=MockLLMService(mock_steps=[]),
        context=LLMContext(),
        workflow=workflow,
        call_context_vars=call_context_vars,
        workflow_run_id=1,
        task=SimpleNamespace(),
        is_voice=is_voice,
    )


async def _agent_prompt(engine, org, agent, workflow) -> str:
    engine._organization_id = org.id
    engine._workflow_id = agent.id
    seen: dict[str, str] = {}

    async def capture(system_prompt, functions):
        seen["prompt"] = system_prompt

    engine._update_llm_context = capture
    with patch.object(engine, "_get_timezone", new=AsyncMock(return_value=None)):
        await engine._setup_llm_context(workflow.nodes[workflow.start_node_id])
    return seen["prompt"]


@pytest.mark.asyncio
class TestTheAgentReadsItsOwn:
    @pytest.mark.parametrize("is_voice", [False, True], ids=["text", "voice"])
    async def test_every_reading_arrives(
        self, db_session, async_session, simple_workflow, is_voice
    ):
        org, _, agent = await _account(async_session, "c")
        await _account(async_session, "qx7neighbour")
        decision = await inbound_guard.evaluate(
            phone_number=SimpleNamespace(
                inbound_contact_list_id=9,
                inbound_require_known_caller=False,
                inbound_max_calls_per_caller=None,
            ),
            caller="+919800000000",
            lookup_contact=AsyncMock(
                return_value=SimpleNamespace(
                    id=4,
                    name="Ramesh Kumar",
                    attributes={"policy_number": "PN-7781", "branch": "Indiranagar"},
                )
            ),
        )
        context_vars = {
            **decision.contact_context,
            "caller_number": "+919800000000",
            "direction": "inbound",
        }
        engine = _engine(simple_workflow, context_vars, is_voice=is_voice)
        prompt = await _agent_prompt(engine, org, agent, simple_workflow)

        assert "Right now it is" in prompt and str(datetime.now(UTC).year) in prompt
        assert "- opening hours c: 9 to 6, closed Sunday" in prompt
        assert "parking c" not in prompt and "delivery c" not in prompt
        assert (_skill().title or _skill().slug) in prompt
        assert "YOUR SCHEDULE." in prompt
        assert "Morning numbers c: Every day at 09:30; on, next run" in prompt
        assert "Summarise yesterday's calls for c" in prompt
        # Decibyl's own routine is not this agent's.
        assert "Weekly board summary" not in prompt
        assert "WHO IS CALLING." in prompt
        assert "- Name: Ramesh Kumar" in prompt
        assert "- policy_number: PN-7781" in prompt
        assert "- branch: Indiranagar" in prompt
        # Routing keys are not the caller's columns.
        assert "direction:" not in prompt
        assert "qx7neighbour" not in prompt

    async def test_nothing_set_is_no_heading(
        self, db_session, async_session, simple_workflow
    ):
        org = OrganizationModel(provider_id="org-bare", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        agent = WorkflowModel(name="Bare", organization_id=org.id, status="active")
        async_session.add(agent)
        await async_session.flush()
        engine = _engine(simple_workflow, {}, is_voice=True)
        prompt = await _agent_prompt(engine, org, agent, simple_workflow)
        assert "YOUR SCHEDULE." not in prompt
        assert "WHO IS CALLING." not in prompt
        assert "Right now it is" in prompt


class TestTheCallerBlock:
    def test_an_unknown_caller_has_none(self):
        assert caller_block({"caller_number": "+91"}) is None
        assert caller_block({"contact_is_known": "yes"}) is None
        assert caller_block(None) is None

    def test_a_run_from_before_the_field_list_still_names_the_caller(self):
        block = caller_block({"contact_is_known": True, "contact_name": "Asha"})
        assert "- Name: Asha" in block

    def test_many_columns_say_how_many_were_left_out(self):
        fields = [f"col{n}" for n in range(20)]
        vars_ = {"contact_is_known": True, "contact_fields": fields}
        vars_.update({f: "x" for f in fields})
        assert "5 more columns not shown here" in caller_block(vars_)


class TestAScheduledRunGetsItsTask:
    def test_the_instruction_is_the_run_s_message(self):
        """The run itself is handed the whole instruction, framed for a room
        with nobody in it (routine_runner)."""
        text = unattended.briefing(
            "Summarise yesterday's calls and list who to call back.",
            writes_allowed=False,
        )
        assert "Summarise yesterday's calls and list who to call back." in text
        assert "This is a scheduled run." in text
