"""Activity -> Usage: per agent and per model, and only this workspace's."""

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import (
    CallCostItemModel,
    OrganizationModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.billing import agent_usage


async def _org(session, name):
    org = OrganizationModel(provider_id=f"usage-{name}-{datetime.now().timestamp()}")
    session.add(org)
    await session.flush()
    return org


async def _agent(session, org, name):
    workflow = WorkflowModel(
        name=name,
        organization_id=org.id,
        workflow_definition={"nodes": [], "edges": []},
    )
    session.add(workflow)
    await session.flush()
    return workflow


async def _run(session, workflow, mode, items):
    run = WorkflowRunModel(name="r", workflow_id=workflow.id, mode=mode)
    session.add(run)
    await session.flush()
    for component, provider, model, units, cost in items:
        session.add(
            CallCostItemModel(
                workflow_run_id=run.id,
                component=component,
                provider=provider,
                model=model,
                units=units,
                cost_paise=cost,
            )
        )
    await session.flush()


@pytest.mark.asyncio
async def test_usage_is_split_by_agent_and_model(db_session, async_session):
    org = await _org(async_session, "mine")
    other = await _org(async_session, "theirs")
    riya = await _agent(async_session, org, "Riya")
    accounts = await _agent(async_session, org, "Accounts")
    stranger = await _agent(async_session, other, "Not mine")

    await _run(
        async_session,
        riya,
        "plivo",
        [
            ("llm_input", "anthropic", "claude-haiku-4-5", 1000, 30),
            ("llm_output", "anthropic", "claude-haiku-4-5", 200, 20),
            ("stt", "sarvam", "saaras:v3", 60, 50),
            ("telephony", "plivo", "", 1, 40),
        ],
    )
    await _run(
        async_session,
        accounts,
        "textchat",
        [("llm_input", "anthropic", "claude-sonnet-5-5", 500, 15)],
    )
    await _run(
        async_session,
        stranger,
        "textchat",
        [("llm_input", "openai", "gpt-5", 9999, 999)],
    )

    now = datetime.now(UTC)
    report = await agent_usage.by_agent(
        async_session,
        organization_id=org.id,
        start=now - timedelta(days=1),
        end=now + timedelta(minutes=1),
    )

    names = [a["name"] for a in report["agents"]]
    assert names == ["Riya", "Accounts"]  # costliest first, and nobody else's
    riya_row = report["agents"][0]
    assert riya_row["runs"] == {"voice": 1}
    assert riya_row["tokens"] == 1200
    assert riya_row["cost_paise"] == 140  # telephony counts toward cost
    haiku = next(m for m in riya_row["models"] if m["model"] == "claude-haiku-4-5")
    assert (haiku["slot"], haiku["tokens"], haiku["cost_paise"]) == ("llm", 1200, 50)
    hearing = next(m for m in riya_row["models"] if m["slot"] == "stt")
    assert hearing["units"] == 60
    assert not any(m["provider"] == "plivo" for m in riya_row["models"])
    assert report["agents"][1]["runs"] == {"text": 1}
    assert report["totals"]["tokens"] == 1700
    assert {m["model"] for m in report["models"]} >= {
        "claude-haiku-4-5",
        "claude-sonnet-5-5",
    }
    assert all(m["model"] != "gpt-5" for m in report["models"])
