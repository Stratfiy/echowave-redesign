"""Seen live: a fresh bot on an account with no credit showed "Done: Hear it
on a call" on its setup rail, though nobody had ever heard it. Its only
runs were chats refused for want of credit, and each refusal leaves a run
row. The rail counted the row.

The rail is a database fact, and the fact it needs is "somebody was on the
line", which the app already defines in one place. Run against the real
database, because the rail's query is the thing under test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.db.models import OrganizationModel, UserModel, WorkflowModel, WorkflowRunModel
from api.services.workflow import setup_progress


async def _bot(session):
    user = UserModel(provider_id="user-rail")
    org = OrganizationModel(provider_id="org-rail", quota_decibyl_tokens=0)
    session.add_all([user, org])
    await session.flush()
    workflow = WorkflowModel(
        name="Front desk",
        user_id=user.id,
        organization_id=org.id,
        workflow_definition={},
        template_context_variables={},
        call_disposition_codes={},
        is_live=True,
    )
    session.add(workflow)
    await session.flush()
    return org, workflow


async def _run(session, workflow, **fields):
    fields.setdefault("mode", "textchat")
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        usage_info={},
        cost_info={},
        initial_context={},
        gathered_context={},
        annotations={},
        is_completed=True,
        **fields,
    )
    session.add(run)
    await session.flush()
    return run


def _tested(progress: setup_progress.SetupProgress) -> bool:
    return next(step for step in progress.steps if step.key == "tested").done


@pytest.mark.asyncio
async def test_a_refused_chat_does_not_tick_hear_it_on_a_call(
    async_session, db_session
):
    org, workflow = await _bot(async_session)
    # A refusal closes the run it opened, and nobody was on it.
    await _run(async_session, workflow)

    progress = await setup_progress.for_workflow(
        workflow_id=workflow.id, organization_id=org.id
    )

    assert _tested(progress) is False
    assert progress.next_step is not None and progress.next_step.key == "tested"


@pytest.mark.asyncio
async def test_a_call_somebody_was_on_does(async_session, db_session):
    org, workflow = await _bot(async_session)
    await _run(async_session, workflow, mode="webrtc", billable_seconds=42)

    progress = await setup_progress.for_workflow(
        workflow_id=workflow.id, organization_id=org.id
    )

    assert _tested(progress) is True


@pytest.mark.asyncio
async def test_an_answered_call_does_too(async_session, db_session):
    org, workflow = await _bot(async_session)
    await _run(async_session, workflow, mode="twilio", answered_at=datetime.now(UTC))

    progress = await setup_progress.for_workflow(
        workflow_id=workflow.id, organization_id=org.id
    )

    assert _tested(progress) is True
