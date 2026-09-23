"""G-1: what the agent graph shows beyond its steps.

What starts an agent is read from the rows that start it -- a number, a
routine, a trigger, a web link, a campaign -- and only this organization's.
The last run's path is read from its own recorded transitions, with the
older name list as a fallback, and a run still in progress is not "last".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from api import constants
from api.db.models import (
    AgentRoutineModel,
    BotTriggerModel,
    CampaignModel,
    EmbedTokenModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.workflow import graph_extras


async def _agent(session, slug):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    agent = WorkflowModel(
        name="Desk", organization_id=org.id, user_id=user.id, status="active"
    )
    session.add(agent)
    await session.flush()
    return org, user, agent


def _transition(node_id, name, prev_id=None, prev_name=None):
    return {
        "type": graph_extras.NODE_TRANSITION,
        "payload": {
            "node_id": node_id,
            "node_name": name,
            "previous_node_id": prev_id,
            "previous_node_name": prev_name,
        },
    }


@pytest.mark.asyncio
class TestWhatStartsIt:
    async def test_every_kind_of_start_is_listed_from_its_own_row(self, async_session):
        org, user, agent = await _agent(async_session, "starts")
        async_session.add_all(
            [
                AgentRoutineModel(
                    organization_id=org.id,
                    workflow_id=agent.id,
                    name="Morning report",
                    cadence="daily",
                    is_active=False,
                ),
                BotTriggerModel(
                    organization_id=org.id,
                    workflow_id=agent.id,
                    uuid="11111111-1111-1111-1111-111111111111",
                    secret="s",
                    name="New Shopify order",
                    source="webhook",
                    sentence="When an order comes in",
                ),
                EmbedTokenModel(
                    token="emb_x",
                    workflow_id=agent.id,
                    organization_id=org.id,
                    created_by=user.id,
                    is_active=True,
                ),
                EmbedTokenModel(
                    token="emb_off",
                    workflow_id=agent.id,
                    organization_id=org.id,
                    created_by=user.id,
                    is_active=False,
                ),
                CampaignModel(
                    name="Diwali reminders",
                    organization_id=org.id,
                    workflow_id=agent.id,
                    created_by=user.id,
                    source_id="file.csv",
                    state="running",
                ),
                CampaignModel(
                    name="Old one",
                    organization_id=org.id,
                    workflow_id=agent.id,
                    created_by=user.id,
                    source_id="old.csv",
                    state="completed",
                ),
            ]
        )
        await async_session.flush()
        found = await graph_extras.starts(
            async_session, organization_id=org.id, workflow_id=agent.id
        )
        kinds = {(f["kind"], f["label"], f["active"]) for f in found}
        assert ("routine", "Morning report", False) in kinds
        assert ("trigger", "New Shopify order", True) in kinds
        assert ("web", "A visitor on your website or share link", True) in kinds
        assert ("campaign", "Campaign: Diwali reminders", True) in kinds
        assert not any("Old one" in f["label"] for f in found)
        assert len([f for f in found if f["kind"] == "web"]) == 1

    async def test_nothing_starts_it_is_an_empty_list(self, async_session):
        org, _, agent = await _agent(async_session, "starts-none")
        assert (
            await graph_extras.starts(
                async_session, organization_id=org.id, workflow_id=agent.id
            )
            == []
        )

    async def test_another_organizations_agent_is_not_answered(self, async_session):
        _, _, theirs = await _agent(async_session, "starts-theirs")
        mine, _, _ = await _agent(async_session, "starts-mine")
        assert (
            await graph_extras.starts(
                async_session, organization_id=mine.id, workflow_id=theirs.id
            )
            is None
        )


@pytest.mark.asyncio
class TestTheLastRun:
    async def test_the_path_comes_from_the_runs_own_transitions(self, async_session):
        org, _, agent = await _agent(async_session, "last")
        now = datetime.now(UTC)
        async_session.add_all(
            [
                WorkflowRunModel(
                    name="older",
                    workflow_id=agent.id,
                    mode="CHAT",
                    is_completed=True,
                    created_at=now - timedelta(hours=2),
                    logs={"realtime_feedback_events": [_transition("x", "Old step")]},
                ),
                WorkflowRunModel(
                    name="latest",
                    workflow_id=agent.id,
                    mode="CHAT",
                    is_completed=True,
                    created_at=now - timedelta(hours=1),
                    logs={
                        "realtime_feedback_events": [
                            {"type": "rtf-user-transcription", "payload": {}},
                            _transition("2", "Book", "1", "Answer"),
                            _transition("3", "Close", "2", "Book"),
                        ]
                    },
                ),
                WorkflowRunModel(
                    name="still going",
                    workflow_id=agent.id,
                    mode="CHAT",
                    is_completed=False,
                    created_at=now,
                    logs={"realtime_feedback_events": [_transition("9", "Nope")]},
                ),
            ]
        )
        await async_session.flush()
        found = await graph_extras.last_run(
            async_session, organization_id=org.id, workflow_id=agent.id
        )
        assert found["run"]["visited_ids"] == ["1", "2", "3"]
        assert found["run"]["visited_names"] == ["Answer", "Book", "Close"]

    async def test_an_older_run_falls_back_to_the_names_it_visited(self, async_session):
        org, _, agent = await _agent(async_session, "last-names")
        async_session.add(
            WorkflowRunModel(
                name="legacy",
                workflow_id=agent.id,
                mode="VOICE",
                is_completed=True,
                gathered_context={"nodes_visited": ["Answer", "Book"]},
            )
        )
        await async_session.flush()
        found = await graph_extras.last_run(
            async_session, organization_id=org.id, workflow_id=agent.id
        )
        assert found["run"]["visited_ids"] == []
        assert found["run"]["visited_names"] == ["Answer", "Book"]

    async def test_no_run_yet_is_said(self, async_session):
        org, _, agent = await _agent(async_session, "last-none")
        assert await graph_extras.last_run(
            async_session, organization_id=org.id, workflow_id=agent.id
        ) == {"run": None}

    async def test_another_organizations_agent_is_not_answered(self, async_session):
        _, _, theirs = await _agent(async_session, "last-theirs")
        mine, _, _ = await _agent(async_session, "last-mine")
        assert (
            await graph_extras.last_run(
                async_session, organization_id=mine.id, workflow_id=theirs.id
            )
            is None
        )


def test_the_routes_are_hidden_while_the_flag_is_off(monkeypatch):
    from api.routes import agent_graph
    from api.services import features

    monkeypatch.setattr(constants, "AGENT_GRAPH_EXTRAS_ENABLED", False)
    with pytest.raises(HTTPException) as caught:
        features.require("agent_graph_extras")()
    assert caught.value.status_code == 404
    for route in agent_graph.router.routes:
        assert any(
            getattr(d.call, "feature", None) == "agent_graph_extras"
            for d in route.dependant.dependencies
        ), route.path
