"""A run's lessons are claimed and written together, or not at all.

``learn_from_run`` used to claim the run (``learned_from`` on its annotations)
in one transaction and write what it taught in a second. A database error or
a worker dying between the two left the run marked learned with nothing
recorded, and every retry skipped it: the gap was lost for good.

These run against Postgres with real commits on separate connections,
because the property is about transactions and row locks, which a mock or
a shared test session cannot show.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db import db_client
from api.db import organisation_fact_client as fact_client
from api.db.models import (
    OrganisationFactModel,
    OrganizationModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.workflow import organisation_learning as learning


@pytest.fixture
async def committed(test_engine):
    """``db_client`` on real, separately committed sessions; an organisation
    with one bot; everything deleted afterwards."""
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    original = db_client.async_session
    db_client.async_session = maker
    async with maker() as session:
        org = OrganizationModel(provider_id="org-learning-atomic")
        session.add(org)
        await session.flush()
        bot = WorkflowModel(name="Front desk", organization_id=org.id)
        session.add(bot)
        await session.flush()
        ids = (org.id, bot.id)
        await session.commit()
    try:
        yield maker, ids
    finally:
        db_client.async_session = original
        async with maker() as session:
            org_id, bot_id = ids
            await session.execute(
                delete(OrganisationFactModel).where(
                    OrganisationFactModel.organization_id == org_id
                )
            )
            await session.execute(
                delete(WorkflowRunModel).where(WorkflowRunModel.workflow_id == bot_id)
            )
            await session.execute(
                delete(WorkflowModel).where(WorkflowModel.id == bot_id)
            )
            await session.execute(
                delete(OrganizationModel).where(OrganizationModel.id == org_id)
            )
            await session.commit()


async def _run(maker, bot_id: int) -> int:
    async with maker() as session:
        run = WorkflowRunModel(name="call", workflow_id=bot_id, mode="textchat")
        session.add(run)
        await session.commit()
        return run.id


async def _gaps(maker, org_id: int) -> list[OrganisationFactModel]:
    async with maker() as session:
        rows = await session.scalars(
            select(OrganisationFactModel).where(
                OrganisationFactModel.organization_id == org_id
            )
        )
        return list(rows.all())


async def _claimed(maker, run_id: int) -> bool:
    async with maker() as session:
        run = await session.get(WorkflowRunModel, run_id)
        return bool((run.annotations or {}).get("learned_from"))


async def _learn(org_id: int, run_id: int) -> int:
    return await learning.learn_from_run(
        organization_id=org_id,
        workflow_run_id=run_id,
        intent=learning.NO_INTENT,
        gathered_context={
            "extracted_variables": {"caller_question": "Do you deliver to Thane?"}
        },
    )


@pytest.mark.asyncio
async def test_a_failure_after_the_claim_is_recoverable(committed):
    """The write fails after the run was claimed in the same transaction:
    nothing commits, the run stays unclaimed, and the retry records it."""
    maker, (org_id, bot_id) = committed
    run_id = await _run(maker, bot_id)

    with patch.object(
        fact_client,
        "_observation_upsert",
        side_effect=RuntimeError("connection dropped mid-write"),
    ):
        assert await _learn(org_id, run_id) == 0

    assert not await _claimed(maker, run_id)
    assert await _gaps(maker, org_id) == []

    assert await _learn(org_id, run_id) == 1
    gaps = await _gaps(maker, org_id)
    assert [(g.subject_key, g.value, g.times_seen) for g in gaps] == [
        (learning.GAP_NOT_UNDERSTOOD, "Do you deliver to Thane?", 1)
    ]
    assert gaps[0].source_run_id == run_id
    assert await _claimed(maker, run_id)


@pytest.mark.asyncio
async def test_a_retry_records_one_observation(committed):
    maker, (org_id, bot_id) = committed
    run_id = await _run(maker, bot_id)

    assert await _learn(org_id, run_id) == 1
    assert await _learn(org_id, run_id) == 0
    assert await _learn(org_id, run_id) == 0

    gaps = await _gaps(maker, org_id)
    assert len(gaps) == 1
    assert gaps[0].times_seen == 1


@pytest.mark.asyncio
async def test_concurrent_processing_never_inflates_times_seen(committed):
    """Five workers on the same run at once count it once; two different
    runs with the same gap count twice, which is the true number."""
    maker, (org_id, bot_id) = committed
    first = await _run(maker, bot_id)
    second = await _run(maker, bot_id)

    results = await asyncio.gather(*(_learn(org_id, first) for _ in range(5)))
    assert sorted(results) == [0, 0, 0, 0, 1]
    gaps = await _gaps(maker, org_id)
    assert [g.times_seen for g in gaps] == [1]

    results = await asyncio.gather(*(_learn(org_id, second) for _ in range(5)))
    assert sorted(results) == [0, 0, 0, 0, 1]
    gaps = await _gaps(maker, org_id)
    assert [g.times_seen for g in gaps] == [2]


@pytest.mark.asyncio
async def test_a_run_that_does_not_exist_teaches_nothing(committed):
    maker, (org_id, _) = committed
    assert await _learn(org_id, 987_654_321) == 0
    assert await _gaps(maker, org_id) == []
