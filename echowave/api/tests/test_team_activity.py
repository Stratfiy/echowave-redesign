"""team_activity: what the other agents in this workspace did.

A read-only tool for a chief-of-staff agent. These tests pin the four things
that matter: it never reads another workspace, it is not offered unless the
owner turned it on (and the flag is on), the numbers are the seeded numbers,
and the answer stays inside its token cap however large the team is.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api import constants
from api.db.models import (
    AgentEventModel,
    CreditLedgerModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import (
    AgentEventActor,
    AgentEventKind,
    AgentEventVisibility,
    CreditLedgerKind,
    OrganizationRole,
)
from api.services import features, prompt_budget
from api.services.billing.credits import PAISE_PER_CREDIT
from api.services.workflow import team_activity
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_context_composer import (
    compose_functions_for_node,
)

NOW = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
RECENT = NOW - timedelta(hours=2)
LAST_WEEK = NOW - timedelta(days=3)
LONG_AGO = NOW - timedelta(days=45)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(constants, "TEAM_ACTIVITY_ENABLED", True)


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setattr(constants, "TEAM_ACTIVITY_ENABLED", False)


async def _org(session, slug: str) -> int:
    org = OrganizationModel(provider_id=f"team-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org.id


async def _user(session, slug: str):
    user = UserModel(provider_id=f"team-user-{slug}", email=f"{slug}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _agent(session, org_id: int, user, name: str, **extra) -> int:
    wf = WorkflowModel(name=name, user_id=user.id, organization_id=org_id, **extra)
    session.add(wf)
    await session.flush()
    return wf.id


async def _run(
    session,
    workflow_id: int,
    *,
    when=RECENT,
    done=True,
    codes=(),
    llm=None,
):
    row = WorkflowRunModel(
        name="run",
        workflow_id=workflow_id,
        mode="chat",
        is_completed=done,
        created_at=when,
        annotations={"disposition": {"dispositions": list(codes)}} if codes else {},
        usage_info={"llm": llm} if llm else {},
    )
    session.add(row)
    await session.flush()
    return row


async def _debit(session, org_id, workflow_id, credits, *, when=RECENT, kind=None):
    session.add(
        CreditLedgerModel(
            organization_id=org_id,
            workflow_id=workflow_id,
            delta_paise=-credits * PAISE_PER_CREDIT,
            kind=kind or CreditLedgerKind.USAGE.value,
            balance_after_paise=0,
            created_at=when,
        )
    )
    await session.flush()


async def _event(
    session,
    org_id,
    workflow_id,
    kind,
    summary="",
    payload=None,
    when=RECENT,
    visibility=AgentEventVisibility.ALWAYS.value,
):
    session.add(
        AgentEventModel(
            organization_id=org_id,
            workflow_id=workflow_id,
            at=when,
            kind=kind.value,
            actor=AgentEventActor.AGENT.value,
            summary=summary,
            payload=payload or {},
            visibility=visibility,
        )
    )
    await session.flush()


async def _ask(session, org_id, caller=None, **arguments):
    result = await team_activity.report(
        session,
        organization_id=org_id,
        arguments=arguments,
        caller_workflow_id=caller,
        now=NOW,
    )
    assert "error" not in result, result
    return result["report"]


async def _seed_team(session):
    """Org A: a chief of staff, Priya (busy) and Ravi (quiet-ish)."""
    org = await _org(session, "a")
    user = await _user(session, "a")
    chief = await _agent(session, org, user, "Chief of staff")
    priya = await _agent(session, org, user, "Priya")
    ravi = await _agent(session, org, user, "Ravi")
    return org, user, chief, priya, ravi


class TestTheFlagAndTheSetting:
    def test_registered_described_and_off_by_default(self):
        assert features.FLAGS["team_activity"] == "TEAM_ACTIVITY_ENABLED"
        assert features.DESCRIPTIONS["team_activity"]
        assert constants.TEAM_ACTIVITY_ENABLED is False

    def test_the_setting_is_off_unless_plainly_true(self):
        assert not team_activity.wants_team(None)
        assert not team_activity.wants_team({})
        assert not team_activity.wants_team({team_activity.CONFIG_KEY: "yes"})
        assert not team_activity.wants_team({team_activity.CONFIG_KEY: 1})
        assert team_activity.wants_team({team_activity.CONFIG_KEY: True})

    def test_offered_needs_the_flag_and_the_setting(self, on, monkeypatch):
        yes = {team_activity.CONFIG_KEY: True}
        assert team_activity.offered(1, yes)
        assert not team_activity.offered(1, {})
        assert not team_activity.offered(1, {team_activity.CONFIG_KEY: False})
        monkeypatch.setattr(constants, "TEAM_ACTIVITY_ENABLED", False)
        assert not team_activity.offered(1, yes)

    def test_a_save_that_does_not_mention_it_keeps_it(self):
        from api.schemas.workflow_configurations import preserve_carried_keys

        kept = preserve_carried_keys({"max_call_duration": 60}, {"can_see_team": True})
        assert kept["can_see_team"] is True
        # An explicit off is a decision and wins.
        off_now = preserve_carried_keys({"can_see_team": False}, {"can_see_team": True})
        assert off_now["can_see_team"] is False

    async def test_the_schema_is_offered_only_when_the_run_may(self):
        node = SimpleNamespace(
            tool_uuids=[],
            document_uuids=[],
            out_edges=[],
            is_end=False,
            is_start=False,
            name="n",
            prompt="p",
        )

        async def names(**flags):
            functions = await compose_functions_for_node(
                node=node, custom_tool_manager=None, **flags
            )
            return [f.name for f in functions]

        assert team_activity.TOOL_NAME not in await names()
        assert team_activity.TOOL_NAME not in await names(can_see_team=False)
        assert team_activity.TOOL_NAME in await names(can_see_team=True)

    async def _engine_says(self, monkeypatch, *, configs, voice=False, flag=True):
        monkeypatch.setattr(constants, "TEAM_ACTIVITY_ENABLED", flag)
        from api.db import db_client

        monkeypatch.setattr(
            db_client,
            "get_workflow",
            AsyncMock(return_value=SimpleNamespace(workflow_configurations=configs)),
        )
        engine = SimpleNamespace(
            _is_voice=voice,
            _sees_team=None,
            _get_organization_id=AsyncMock(return_value=7),
            _get_workflow_id=AsyncMock(return_value=11),
        )
        return await PipecatEngine._can_see_team(engine)

    async def test_the_engine_does_not_offer_it_with_the_setting_off(self, monkeypatch):
        assert not await self._engine_says(monkeypatch, configs={})
        assert not await self._engine_says(
            monkeypatch, configs={team_activity.CONFIG_KEY: False}
        )

    async def test_the_engine_offers_it_with_the_setting_and_flag_on(self, monkeypatch):
        yes = {team_activity.CONFIG_KEY: True}
        assert await self._engine_says(monkeypatch, configs=yes)

    async def test_the_engine_does_not_offer_it_with_the_flag_off(self, monkeypatch):
        yes = {team_activity.CONFIG_KEY: True}
        assert not await self._engine_says(monkeypatch, configs=yes, flag=False)

    async def test_the_engine_does_not_offer_it_on_a_call(self, monkeypatch):
        yes = {team_activity.CONFIG_KEY: True}
        assert not await self._engine_says(monkeypatch, configs=yes, voice=True)


class TestTheNumbers:
    async def test_runs_outcomes_credits_and_tokens_are_the_seeded_ones(
        self, async_session
    ):
        s = async_session
        org, _, chief, priya, _ravi = await _seed_team(s)
        # Priya: 3 runs in the window (2 finished), one outside it.
        await _run(
            s,
            priya,
            codes=["booked"],
            llm={"a|||m": {"prompt_tokens": 1000, "completion_tokens": 500}},
        )
        await _run(
            s,
            priya,
            codes=["booked", "callback"],
            llm={"a|||m": {"total_tokens": 2000, "prompt_tokens": 1}},
        )
        await _run(s, priya, done=False, when=LAST_WEEK)
        await _run(s, priya, when=LONG_AGO, llm={"a|||m": {"total_tokens": 99_999}})
        # Ledger: 12 + 8 credits in the window, one old, a top-up, and the
        # chief's own spend, none of which are Priya's usage this week.
        await _debit(s, org, priya, 12)
        await _debit(s, org, priya, 8, when=LAST_WEEK)
        await _debit(s, org, priya, 50, when=LONG_AGO)
        await _debit(s, org, priya, 5, kind=CreditLedgerKind.ADJUSTMENT.value)
        await _debit(s, org, chief, 77)
        # Ravi did nothing.

        text = await _ask(s, org, caller=chief, range="7d")

        priya_line = next(l for l in text.splitlines() if "Priya" in l)
        assert "3 runs (2 finished)" in priya_line
        assert "booked 2" in priya_line and "callback 1" in priya_line
        assert "20 credits" in priya_line
        assert "3.5k tokens" in priya_line  # 1500 + 2000, the old run excluded
        assert "Ravi" in text and "Quiet: Ravi" in text
        assert "Chief of staff" not in text  # the caller is not its own team
        assert "1 of 2 agents active" in text

    async def test_today_means_since_midnight_india_time(self, async_session):
        s = async_session
        org, _, chief, priya, _ = await _seed_team(s)
        # NOW is 14:30 IST on 10 Oct; 18:00 UTC on the 9th is 23:30 IST on the 9th.
        await _run(s, priya, when=NOW - timedelta(hours=1))
        await _run(s, priya, when=datetime(2026, 10, 9, 18, 0, tzinfo=UTC))
        text = await _ask(s, org, caller=chief, range="today")
        assert "1 runs (1 finished)" in text

    async def test_usage_is_read_through_one_function(self, async_session):
        s = async_session
        org, _, _, priya, _ = await _seed_team(s)
        await _debit(s, org, priya, 4)
        await _run(s, priya, llm={"m": {"total_tokens": 300}})
        got = await team_activity.usage_by_agent(
            s,
            organization_id=org,
            workflow_ids=[priya],
            since=NOW - timedelta(days=7),
        )
        assert (got[priya].credits, got[priya].tokens) == (4, 300)

    async def test_unreadable_usage_info_costs_that_run_only(self, async_session):
        s = async_session
        org, _, chief, priya, _ = await _seed_team(s)
        for bad in ("text", [1], {"llm": "x"}, {"llm": {"a": "x", "b": None}}):
            row = await _run(s, priya)
            row.usage_info = bad
        await _run(s, priya, llm={"m": {"total_tokens": 10}})
        await s.flush()
        text = await _ask(s, org, caller=chief)
        assert "5 runs" in text and "10 tokens" in text

    async def test_failures_files_and_waiting_approvals_are_listed(self, async_session):
        s = async_session
        org, _, chief, priya, ravi = await _seed_team(s)
        await _event(
            s,
            org,
            priya,
            AgentEventKind.COULD_NOT,
            "Morning digest could not finish its run",
        )
        await _event(
            s,
            org,
            ravi,
            AgentEventKind.DELIVERABLE,
            "PO drafted",
            {
                "attachments": [
                    {"document_uuid": "procurement-5-docx", "filename": "PO-014.docx"},
                    {"document_uuid": "procurement-5-pdf", "filename": "PO-014.pdf"},
                ]
            },
        )
        await _event(s, org, ravi, AgentEventKind.DELIVERABLE, "Weekly summary text")
        # Two cards waiting, one already done.
        await _event(
            s,
            org,
            ravi,
            AgentEventKind.ACTION_PROPOSED,
            "",
            {"action": "send_document"},
        )
        await _event(
            s, org, ravi, AgentEventKind.ACTION_PROPOSED, "", {"action": "run_tool"}
        )
        await _event(
            s,
            org,
            ravi,
            AgentEventKind.ACTION_PROPOSED,
            "",
            {"action": "run_tool", "state": "done"},
        )
        # A card proposed a month ago and never answered is still waiting.
        await _event(
            s,
            org,
            ravi,
            AgentEventKind.ACTION_PROPOSED,
            "",
            {"action": "send_document"},
            when=LONG_AGO,
        )

        text = await _ask(s, org, caller=chief)

        ravi_line = next(l for l in text.splitlines() if "Ravi" in l)
        assert "PO-014.docx (procurement-5-docx)" in ravi_line
        assert "PO-014.pdf (procurement-5-pdf)" in ravi_line
        assert "1 report(s)" in ravi_line
        assert "3 approval(s) waiting" in ravi_line
        assert "send_document x2" in ravi_line
        assert "Failures (1)" in text
        assert "Priya" in text.split("Failures")[1]
        assert "Morning digest could not finish its run" in text

    async def test_one_agent_by_name(self, async_session):
        s = async_session
        org, _, chief, priya, ravi = await _seed_team(s)
        await _run(s, priya)
        await _run(s, ravi)
        text = await _ask(s, org, caller=chief, agent="priy")
        assert "Priya" in text and "Ravi" not in text
        gone = await _ask(s, org, caller=chief, agent="Nobody")
        assert "No agent here is called" in gone and "Priya, Ravi" in gone

    async def test_a_bad_range_is_refused_not_guessed(self, async_session):
        s = async_session
        org, *_ = await _seed_team(s)
        result = await team_activity.report(
            s, organization_id=org, arguments={"range": "90d"}, now=NOW
        )
        assert "error" in result


class TestWhatItWillNotShow:
    async def test_another_workspace_is_never_read(self, async_session):
        s = async_session
        org, _, chief, priya, _ = await _seed_team(s)
        other = await _org(s, "b")
        user_b = await _user(s, "b")
        spy = await _agent(s, other, user_b, "Spy")
        await _run(s, spy, codes=["stolen"], llm={"m": {"total_tokens": 424_242}})
        await _debit(s, other, spy, 999)
        await _event(s, other, spy, AgentEventKind.COULD_NOT, "Secret failure in B")
        await _event(
            s,
            other,
            spy,
            AgentEventKind.DELIVERABLE,
            "x",
            {"attachments": [{"document_uuid": "b-doc", "filename": "b-secret.docx"}]},
        )
        await _event(
            s, other, spy, AgentEventKind.ACTION_PROPOSED, "", {"action": "b_act"}
        )
        await _run(s, priya)

        text = await _ask(s, org, caller=chief)
        for leak in (
            "Spy",
            "stolen",
            "424",
            "999",
            "Secret failure",
            "b-secret",
            "b_act",
        ):
            assert leak not in text
        # Asking for it by name finds nothing, rather than finding it.
        by_name = await _ask(s, org, caller=chief, agent="Spy")
        assert (
            "No agent here is called" in by_name
            and "Spy" not in by_name.split("Agents:")[1]
        )
        # And the other workspace sees only itself.
        mine = await _ask(s, other)
        assert "Spy" in mine and "Priya" not in mine

    async def test_an_agent_cannot_point_the_tool_at_another_workspace(
        self, async_session
    ):
        s = async_session
        org, _, chief, _priya, _ = await _seed_team(s)
        other = await _org(s, "b2")
        spy = await _agent(s, other, await _user(s, "b2"), "Spy2")
        await _run(s, spy)
        # There is no such argument, and the ids the model might guess are
        # filtered by the run's own workspace.
        text = await _ask(s, org, caller=chief, organization_id=other, workflow_id=spy)
        assert "Spy2" not in text

    async def test_a_row_for_one_person_is_not_the_teams(self, async_session):
        s = async_session
        org, _, chief, priya, _ = await _seed_team(s)
        await _event(
            s, org, priya, AgentEventKind.COULD_NOT, "private note", {"private_to": 5}
        )
        await _event(
            s,
            org,
            priya,
            AgentEventKind.COULD_NOT,
            "caller words",
            visibility=AgentEventVisibility.ON_REQUEST.value,
        )
        await _event(
            s,
            org,
            priya,
            AgentEventKind.ACTION_PROPOSED,
            "",
            {"action": "place_order", "owner_user_id": 5},
        )
        text = await _ask(s, org, caller=chief)
        assert "private note" not in text and "caller words" not in text
        assert "waiting" not in text

    async def test_an_admin_only_agent_is_left_out_and_said_so(self, async_session):
        s = async_session
        org, user, chief, _priya, _ = await _seed_team(s)
        hidden = await _agent(s, org, user, "Board prep", visibility="admins")
        await _run(s, hidden)
        text = await _ask(s, org, caller=chief)
        assert "Board prep" not in text
        assert "1 admin-only agent(s) are not covered" in text

    async def test_an_archived_agent_is_not_counted(self, async_session):
        s = async_session
        org, user, chief, *_ = await _seed_team(s)
        old = await _agent(s, org, user, "Retired", status="archived")
        await _run(s, old)
        assert "Retired" not in await _ask(s, org, caller=chief)


class TestTheSizeCap:
    async def test_a_large_team_stays_inside_the_token_cap(self, async_session):
        s = async_session
        org = await _org(s, "big")
        user = await _user(s, "big")
        for i in range(120):
            wid = await _agent(s, org, user, f"Agent number {i:03d} with a long name")
            await _run(s, wid, codes=["booked", "callback", "other"])
            await _debit(s, org, wid, i + 1)
            await _event(
                s,
                org,
                wid,
                AgentEventKind.COULD_NOT,
                "x" * 450,
            )
            await _event(
                s,
                org,
                wid,
                AgentEventKind.DELIVERABLE,
                "y",
                {
                    "attachments": [
                        {
                            "document_uuid": f"doc-{i}-{n}",
                            "filename": f"file-{i}-{n}.docx",
                        }
                        for n in range(10)
                    ]
                },
            )

        text = await _ask(s, org, range="30d")

        assert prompt_budget.estimate(text) <= team_activity.MAX_TOKENS
        assert "not shown to stay short" in text
        assert "Failures (120, newest 8)" in text
        # A reason is cut, with the cut visible.
        assert "x" * 200 not in text and "…" in text

    async def test_a_small_team_is_not_cut(self, async_session):
        s = async_session
        org, _, chief, priya, _ = await _seed_team(s)
        await _run(s, priya)
        text = await _ask(s, org, caller=chief)
        assert "not shown" not in text
        assert prompt_budget.estimate(text) < team_activity.MAX_TOKENS


class TestTheToolCall:
    async def test_run_answers_for_the_runs_workspace(self, db_session, async_session):
        org, _, chief, priya, _ = await _seed_team(async_session)
        await _run(async_session, priya)
        result = await team_activity.run(
            org, {"range": "30d"}, caller_workflow_id=chief
        )
        assert "Priya" in result["report"]

    async def test_run_never_raises(self, db_session, async_session):
        result = await team_activity.run(None, {}, caller_workflow_id=None)
        assert "error" in result


class TestTheSettingRoute:
    async def test_off_the_route_is_a_404(
        self, off, test_client_factory, db_session, async_session
    ):
        org = await _org(async_session, "route-off")
        user = await _user(async_session, "route-off")
        user.selected_organization_id = org
        wid = await _agent(async_session, org, user, "A")
        async with test_client_factory(user) as client:
            answer = await client.get(f"/api/v1/workflow/{wid}/team-access")
        assert answer.status_code == 404

    async def test_the_owner_turns_it_on_and_off_and_nothing_else_changes(
        self, on, test_client_factory, db_session, async_session
    ):
        org = await _org(async_session, "route-on")
        user = await _user(async_session, "route-on")
        user.selected_organization_id = org
        await db_session.add_user_to_organization(
            user.id, org, role=OrganizationRole.ADMIN.value
        )
        wid = await _agent(
            async_session,
            org,
            user,
            "A",
            workflow_configurations={"channel": "chat", "notify_on": ["escalated"]},
        )
        async with test_client_factory(user) as client:
            first = await client.get(f"/api/v1/workflow/{wid}/team-access")
            assert first.json() == {"enabled": False}
            put = await client.put(
                f"/api/v1/workflow/{wid}/team-access", json={"enabled": True}
            )
            assert put.status_code == 200 and put.json() == {"enabled": True}
            again = await client.get(f"/api/v1/workflow/{wid}/team-access")
            assert again.json() == {"enabled": True}
            await client.put(
                f"/api/v1/workflow/{wid}/team-access", json={"enabled": False}
            )
            final = await client.get(f"/api/v1/workflow/{wid}/team-access")
            assert final.json() == {"enabled": False}
        row = await async_session.scalar(
            select(WorkflowModel).where(WorkflowModel.id == wid)
        )
        assert row.workflow_configurations["channel"] == "chat"
        assert row.workflow_configurations["notify_on"] == ["escalated"]

    async def test_another_workspaces_agent_is_not_found(
        self, on, test_client_factory, db_session, async_session
    ):
        mine = await _org(async_session, "route-mine")
        theirs = await _org(async_session, "route-theirs")
        user = await _user(async_session, "route-x")
        user.selected_organization_id = mine
        owner = await _user(async_session, "route-y")
        wid = await _agent(async_session, theirs, owner, "Theirs")
        async with test_client_factory(user) as client:
            got = await client.get(f"/api/v1/workflow/{wid}/team-access")
            put = await client.put(
                f"/api/v1/workflow/{wid}/team-access", json={"enabled": True}
            )
        assert got.status_code == 404 and put.status_code == 404
