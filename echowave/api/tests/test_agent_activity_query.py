"""The home screen's per-agent activity, run against the real database.

`agent_activity` filters out test runs by reading a key of the run's
`annotations` column. The column is JSON, not JSONB, and the JSONB-only
`.astext` accessor raised on every read, so `/team/status` and `/team/home`
answered 500 for every account. The unit tests around it mock the database
and never saw it. This one does not mock the database.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.db.models import OrganizationModel, UserModel, WorkflowModel, WorkflowRunModel
from api.services.workflow import test_runs


async def _run(session, workflow, *, mode: str, annotations: dict | None = None):
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode=mode,
        usage_info={},
        cost_info={},
        initial_context={},
        gathered_context={},
        annotations=annotations or {},
        is_completed=True,
        answered_at=datetime.now(UTC) if mode == "twilio" else None,
    )
    session.add(run)
    await session.flush()
    return run


@pytest.mark.asyncio
async def test_a_business_call_counts_and_a_test_does_not(async_session, db_session):
    user = UserModel(provider_id="user-activity")
    org = OrganizationModel(provider_id="org-activity", quota_decibyl_tokens=0)
    async_session.add_all([user, org])
    await async_session.flush()
    workflow = WorkflowModel(
        name="Front desk",
        user_id=user.id,
        organization_id=org.id,
        workflow_definition={},
        template_context_variables={},
        call_disposition_codes={},
    )
    async_session.add(workflow)
    await async_session.flush()

    await _run(async_session, workflow, mode="twilio")
    await _run(async_session, workflow, mode="webrtc")
    await _run(
        async_session,
        workflow,
        mode="twilio",
        annotations=test_runs.stamp("thread", "text"),
    )

    activity = await db_session.agent_activity(organization_id=org.id, hours=24)

    assert activity[workflow.id]["calls"] == 1
    assert activity[workflow.id]["answered"] == 1
