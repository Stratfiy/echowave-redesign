"""Stream ops, handoff 34 and the design's "Operational action" contract.

What these defend: only allowlisted commands exist; role, environment and
target schema are checked before anything is stored; the same idempotency
key returns the same request and a reused key with a different target is a
conflict; reads run inline, mutations are queued (accepted is not
succeeded) and run exactly once; approval needs a second person of the
right role and expires; a pause can only touch a row in the named
workspace; a flag change is versioned and can be rolled back; an
infrastructure runbook with nothing configured says ``needs_setup``; a timed
pause lifts itself; and the routes answer 202 / 403 / 409 accordingly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api import constants
from api.db.models import (
    AdminActionLogModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
)
from api.db.ops_models import OpsCommandModel
from api.enums import StaffRole
from api.services import features
from api.services.ops import commands


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    features.clear_snapshot()
    monkeypatch.setattr(constants, "OPS_CONSOLE_ENABLED", False)
    monkeypatch.setattr(constants, "PROJECTS_ENABLED", False)
    monkeypatch.setattr(constants, "OPS_SSM_DOCUMENTS", "")

    async def _publish(_org):
        return None

    monkeypatch.setattr("api.services.feature_admin.publish_change", _publish)
    yield
    features.clear_snapshot()


class Queue:
    def __init__(self):
        self.ids: list[int] = []

    async def __call__(self, command_id: int) -> None:
        self.ids.append(command_id)


async def _user(session, slug, role):
    user = UserModel(provider_id=f"ops-cmd-{slug}", staff_role=role)
    session.add(user)
    await session.flush()
    return user


async def _org(session, slug):
    org = OrganizationModel(provider_id=f"ops-cmd-org-{slug}")
    session.add(org)
    await session.flush()
    return org


async def _agent(session, org, owner):
    agent = WorkflowModel(
        name="Front desk",
        user_id=owner.id,
        organization_id=org.id,
        workflow_definition={},
    )
    session.add(agent)
    await session.flush()
    return agent


@pytest.mark.asyncio
async def test_only_allowlisted_commands_with_the_right_role(db_session, async_session):
    support = await _user(async_session, "support", StaffRole.SUPPORT.value)
    plain = await _user(async_session, "plain", None)
    with pytest.raises(commands.CommandError):
        await commands.request(
            async_session,
            user=support,
            command="shell.exec",
            target={"cmd": "rm -rf /"},
            reason="please",
            idempotency_key="key-00000001",
        )
    with pytest.raises(commands.NotPermitted):
        await commands.request(
            async_session,
            user=plain,
            command="queue.inspect",
            target={},
            reason="look",
            idempotency_key="key-00000002",
        )
    with pytest.raises(commands.NotPermitted):
        await commands.request(
            async_session,
            user=support,
            command="flag.set",
            target={"feature": "projects", "enabled": True},
            reason="try it",
            idempotency_key="key-00000003",
        )
    with pytest.raises(commands.NotPermitted):
        await commands.request(
            async_session,
            user=support,
            command="queue.inspect",
            target={},
            reason="look",
            idempotency_key="key-00000004",
            environment="production",
        )
    with pytest.raises(commands.CommandError, match="Invalid target"):
        await commands.request(
            async_session,
            user=support,
            command="agent.pause",
            target={"organization_id": 1, "workflow_id": 2, "sql": "drop"},
            reason="stop it",
            idempotency_key="key-00000005",
        )
    assert (await async_session.scalars(select(OpsCommandModel))).all() == []


@pytest.mark.asyncio
async def test_reads_run_inline_and_say_succeeded(
    db_session, async_session, monkeypatch
):
    support = await _user(async_session, "reader", StaffRole.SUPPORT.value)

    async def fake(_session, _target, _row):
        return {"queue": {"depth": 0}}

    spec = commands.REGISTRY["queue.inspect"]
    monkeypatch.setitem(
        commands.REGISTRY,
        "queue.inspect",
        commands.CommandSpec(**{**spec.__dict__, "handler": fake}),
    )
    view = await commands.request(
        async_session,
        user=support,
        command="queue.inspect",
        target={},
        reason="morning check",
        idempotency_key="read-00000001",
    )
    assert view.state == commands.SUCCEEDED
    assert view.result == {"queue": {"depth": 0}}


@pytest.mark.asyncio
async def test_mutation_is_queued_runs_once_and_is_scoped(db_session, async_session):
    support = await _user(async_session, "pauser", StaffRole.SUPPORT.value)
    mine = await _org(async_session, "mine")
    theirs = await _org(async_session, "theirs")
    agent = await _agent(async_session, mine, support)
    queue = Queue()

    with pytest.raises(commands.CommandError, match="No such agent"):
        await commands.request(
            async_session,
            user=support,
            command="agent.pause",
            target={"organization_id": theirs.id, "workflow_id": agent.id},
            reason="runaway",
            idempotency_key="pause-other-org",
            enqueue=queue,
        )

    view = await commands.request(
        async_session,
        user=support,
        command="agent.pause",
        target={"organization_id": mine.id, "workflow_id": agent.id},
        reason="runaway loop",
        idempotency_key="pause-00000001",
        enqueue=queue,
    )
    assert view.state == commands.QUEUED
    assert view.preview["currently_live"] is True
    assert queue.ids == [view.id]
    await async_session.refresh(agent)
    assert agent.is_live is True  # accepted is not done

    done = await commands.execute(async_session, view.id)
    assert done.state == commands.SUCCEEDED
    assert await commands.execute(async_session, view.id) is None  # runs once
    await async_session.refresh(agent)
    assert agent.is_live is False

    same = await commands.request(
        async_session,
        user=support,
        command="agent.pause",
        target={"organization_id": mine.id, "workflow_id": agent.id},
        reason="again",
        idempotency_key="pause-00000001",
        enqueue=queue,
    )
    assert same.id == view.id
    with pytest.raises(commands.Conflict):
        await commands.request(
            async_session,
            user=support,
            command="agent.resume",
            target={"organization_id": mine.id, "workflow_id": agent.id},
            reason="again",
            idempotency_key="pause-00000001",
            enqueue=queue,
        )
    notes = [
        r.action
        for r in (await async_session.scalars(select(AdminActionLogModel))).all()
    ]
    assert "ops_command_requested" in notes and "ops_command_succeeded" in notes


@pytest.mark.asyncio
async def test_approval_needs_a_second_person_and_rollback_restores(
    db_session, async_session
):
    first = await _user(async_session, "admin-a", StaffRole.SUPERADMIN.value)
    second = await _user(async_session, "admin-b", StaffRole.SUPERADMIN.value)
    support = await _user(async_session, "support-b", StaffRole.SUPPORT.value)
    org = await _org(async_session, "flagged")
    queue = Queue()
    view = await commands.request(
        async_session,
        user=first,
        command="flag.set",
        target={"feature": "projects", "organization_id": org.id, "enabled": True},
        reason="pilot",
        idempotency_key="flag-00000001",
        enqueue=queue,
    )
    assert view.state == commands.AWAITING_APPROVAL
    assert view.preview["currently_on"] is False and view.preview["will_be_on"] is True
    assert queue.ids == []
    with pytest.raises(commands.NotPermitted):
        await commands.approve(
            async_session, user=first, command_id=view.id, enqueue=queue
        )
    with pytest.raises(commands.NotPermitted):
        await commands.approve(
            async_session, user=support, command_id=view.id, enqueue=queue
        )
    approved = await commands.approve(
        async_session, user=second, command_id=view.id, enqueue=queue
    )
    assert approved.state == commands.QUEUED and queue.ids == [view.id]
    done = await commands.execute(async_session, view.id)
    assert done.state == commands.SUCCEEDED
    assert done.result["previous"] is None
    await features.refresh_overrides()
    assert features.is_on("projects", org.id) is True

    rollback = await commands.request(
        async_session,
        user=first,
        command="flag.rollback",
        target={"command_id": view.id},
        reason="pilot over",
        idempotency_key="flag-rollback-1",
        enqueue=queue,
    )
    await commands.execute(async_session, rollback.id)
    await features.refresh_overrides()
    assert features.is_on("projects", org.id) is False


@pytest.mark.asyncio
async def test_reject_and_expire(db_session, async_session):
    first = await _user(async_session, "admin-c", StaffRole.SUPERADMIN.value)
    second = await _user(async_session, "admin-d", StaffRole.SUPERADMIN.value)
    queue = Queue()
    a = await commands.request(
        async_session,
        user=first,
        command="laya.restore",
        target={},
        reason="evaluation passed",
        idempotency_key="laya-restore-1",
        enqueue=queue,
    )
    rejected = await commands.reject(
        async_session, user=second, command_id=a.id, note="not yet"
    )
    assert rejected.state == commands.REJECTED
    b = await commands.request(
        async_session,
        user=first,
        command="laya.restore",
        target={},
        reason="evaluation passed",
        idempotency_key="laya-restore-2",
        enqueue=queue,
    )
    swept = await commands.expire_and_lift(
        async_session, now=datetime.now(UTC) + timedelta(hours=2)
    )
    assert swept["expired"] == 1
    assert (await commands.get(async_session, b.id)).state == commands.EXPIRED


@pytest.mark.asyncio
async def test_laya_rollback_is_fast_and_flips_the_switch(db_session, async_session):
    support = await _user(async_session, "oncall", StaffRole.SUPPORT.value)
    queue = Queue()
    view = await commands.request(
        async_session,
        user=support,
        command="laya.rollback",
        target={},
        reason="bad routing",
        idempotency_key="laya-rb-0001",
        enqueue=queue,
    )
    assert view.state == commands.QUEUED  # no approval in the safe direction
    await commands.execute(async_session, view.id)
    await features.refresh_overrides()
    assert features.is_on("laya_rollback") is True


@pytest.mark.asyncio
async def test_unconfigured_runbook_is_needs_setup(db_session, async_session):
    first = await _user(async_session, "admin-e", StaffRole.SUPERADMIN.value)
    second = await _user(async_session, "admin-f", StaffRole.SUPERADMIN.value)
    queue = Queue()
    view = await commands.request(
        async_session,
        user=first,
        command="workers.drain",
        target={"group": "worker"},
        reason="deploy",
        idempotency_key="drain-00000001",
        enqueue=queue,
    )
    await commands.approve(
        async_session, user=second, command_id=view.id, enqueue=queue
    )
    done = await commands.execute(async_session, view.id)
    assert done.state == commands.NEEDS_SETUP
    assert "OPS_SSM_DOCUMENTS" in done.result["detail"]


@pytest.mark.asyncio
async def test_runbook_starts_and_refreshes(db_session, async_session, monkeypatch):
    from api.services.ops import infrastructure

    monkeypatch.setattr(
        constants, "OPS_SSM_DOCUMENTS", "workers.restart=Decibyl-RestartWorkers"
    )
    monkeypatch.setattr(constants, "OPS_SSM_TARGET_INSTANCE_ID", "i-0abc")

    class Client:
        def __init__(self):
            self.started = []

        def start_automation_execution(self, **kwargs):
            self.started.append(kwargs)
            return {"AutomationExecutionId": "exec-1"}

        def get_automation_execution(self, **kwargs):
            return {
                "AutomationExecution": {
                    "AutomationExecutionStatus": "Success",
                    "StepExecutions": [{"StepName": "drain", "StepStatus": "Success"}],
                }
            }

    client = Client()
    runner = infrastructure.SsmAutomationRunner(client)
    monkeypatch.setattr(infrastructure, "get_runner", lambda: runner)
    first = await _user(async_session, "admin-g", StaffRole.SUPERADMIN.value)
    second = await _user(async_session, "admin-h", StaffRole.SUPERADMIN.value)
    queue = Queue()
    view = await commands.request(
        async_session,
        user=first,
        command="workers.restart",
        target={"group": "api", "drain_timeout_seconds": 120},
        reason="memory leak",
        idempotency_key="restart-0000001",
        enqueue=queue,
    )
    await commands.approve(
        async_session, user=second, command_id=view.id, enqueue=queue
    )
    started = await commands.execute(async_session, view.id)
    assert started.state == commands.RUNNING
    assert client.started[0]["DocumentName"] == "Decibyl-RestartWorkers"
    assert client.started[0]["Parameters"]["InstanceId"] == ["i-0abc"]
    finished = await commands.refresh(async_session, view.id, runner=runner)
    assert finished.state == commands.SUCCEEDED


@pytest.mark.asyncio
async def test_a_timed_pause_lifts_itself(db_session, async_session):
    support = await _user(async_session, "timer", StaffRole.SUPPORT.value)
    org = await _org(async_session, "timed")
    agent = await _agent(async_session, org, support)
    view = await commands.request(
        async_session,
        user=support,
        command="agent.pause",
        target={
            "organization_id": org.id,
            "workflow_id": agent.id,
            "expires_in_minutes": 30,
        },
        reason="cooling off",
        idempotency_key="timed-pause-01",
        enqueue=Queue(),
    )
    await commands.execute(async_session, view.id)
    await async_session.refresh(agent)
    assert agent.is_live is False
    swept = await commands.expire_and_lift(
        async_session, now=datetime.now(UTC) + timedelta(hours=1)
    )
    assert swept["lifted"] == 1
    await async_session.refresh(agent)
    assert agent.is_live is True
    again = await commands.expire_and_lift(
        async_session, now=datetime.now(UTC) + timedelta(hours=2)
    )
    assert again["lifted"] == 0


def test_catalogue_describes_every_command():
    rows = commands.catalogue()
    assert {r["name"] for r in rows} == set(commands.REGISTRY)
    for row in rows:
        assert row["target_schema"]["type"] == "object"
        assert row["min_role"] in ("support", "superadmin")


@pytest.fixture
def as_user(monkeypatch, test_client_factory):
    def _make(user):
        async def _fake_get_user(*_args, **_kwargs):
            return user

        monkeypatch.setattr("api.services.auth.depends.get_user", _fake_get_user)
        return test_client_factory(user)

    return _make


@pytest.mark.asyncio
async def test_routes(db_session, async_session, as_user, monkeypatch):
    queue = Queue()
    monkeypatch.setattr(commands, "_enqueue_default", queue)
    support = await _user(async_session, "route-support", StaffRole.SUPPORT.value)
    org = await _org(async_session, "route")
    agent = await _agent(async_session, org, support)
    body = {
        "command": "agent.pause",
        "target": {"organization_id": org.id, "workflow_id": agent.id},
        "reason": "route test",
        "idempotency_key": "route-pause-0001",
    }
    async with as_user(support) as client:
        assert (
            await client.post("/api/v1/admin/ops/commands", json=body)
        ).status_code == 404
        monkeypatch.setattr(constants, "OPS_CONSOLE_ENABLED", True)
        accepted = await client.post("/api/v1/admin/ops/commands", json=body)
        assert accepted.status_code == 202
        assert accepted.json()["state"] == "queued"
        conflict = await client.post(
            "/api/v1/admin/ops/commands",
            json={
                **body,
                "target": {"organization_id": org.id, "workflow_id": agent.id + 1},
            },
        )
        assert conflict.status_code == 409
        forbidden = await client.post(
            "/api/v1/admin/ops/commands",
            json={
                "command": "flag.set",
                "target": {"feature": "projects", "enabled": True},
                "reason": "nope",
                "idempotency_key": "route-flag-0001",
            },
        )
        assert forbidden.status_code == 403
        catalogue = await client.get("/api/v1/admin/ops/commands/catalogue")
        assert catalogue.status_code == 200
        listed = await client.get("/api/v1/admin/ops/commands")
        assert listed.json()["commands"][0]["id"] == accepted.json()["id"]


@pytest.mark.asyncio
async def test_accepting_survives_a_session_that_expires_on_commit(
    db_session, async_session, monkeypatch
):
    """The production session expires every row on commit; reading the
    accepted row afterwards raised MissingGreenlet (found on a running
    instance, not by the transactional test session)."""
    original = async_session.commit

    async def commit_and_expire():
        await original()
        async_session.expire_all()

    monkeypatch.setattr(async_session, "commit", commit_and_expire)
    first = await _user(async_session, "expire-a", StaffRole.SUPERADMIN.value)
    second = await _user(async_session, "expire-b", StaffRole.SUPERADMIN.value)
    queue = Queue()
    view = await commands.request(
        async_session,
        user=first,
        command="laya.rollback",
        target={},
        reason="incident",
        idempotency_key="expire-0000001",
        enqueue=queue,
    )
    assert view.state == commands.QUEUED and queue.ids == [view.id]
    # The staff users came from this session too; in a request they come
    # from the auth dependency's own session and are not expired here.
    await async_session.refresh(first)
    await async_session.refresh(second)
    asked = await commands.request(
        async_session,
        user=first,
        command="laya.restore",
        target={},
        reason="recovered",
        idempotency_key="expire-0000002",
        enqueue=queue,
    )
    approved = await commands.approve(
        async_session, user=second, command_id=asked.id, enqueue=queue
    )
    assert approved.state == commands.QUEUED and queue.ids[-1] == asked.id


@pytest.mark.asyncio
async def test_a_flag_change_is_announced_after_commit_from_the_worker(
    db_session, async_session, monkeypatch
):
    """The ARQ worker has no worker sync manager; the change still reaches
    the API workers, and only once it is committed."""
    from api.tasks import ops as ops_tasks

    published = []

    class FakeClient:
        async def publish(self, channel, message):
            published.append((channel, message))

        async def aclose(self):
            pass

    monkeypatch.setattr("redis.asyncio.from_url", lambda *_a, **_k: FakeClient())

    def no_manager():
        raise RuntimeError("no manager in the ARQ worker")

    monkeypatch.setattr(
        "api.services.worker_sync.manager.get_worker_sync_manager", no_manager
    )
    support = await _user(async_session, "worker-announce", StaffRole.SUPPORT.value)
    view = await commands.request(
        async_session,
        user=support,
        command="laya.rollback",
        target={},
        reason="incident",
        idempotency_key="announce-00001",
        enqueue=Queue(),
    )
    await ops_tasks.run_ops_command({}, view.id)
    assert (await commands.get(async_session, view.id)).state == commands.SUCCEEDED
    assert len(published) == 1
    assert '"feature_overrides"' in published[0][1]
    assert features.is_on("laya_rollback") is True


@pytest.mark.asyncio
async def test_a_lost_enqueue_is_retried_and_a_dead_run_is_outcome_unknown(
    db_session, async_session
):
    support = await _user(async_session, "sweeper", StaffRole.SUPPORT.value)
    lost = Queue()
    view = await commands.request(
        async_session,
        user=support,
        command="laya.rollback",
        target={},
        reason="incident",
        idempotency_key="lost-enqueue-01",
        enqueue=lost,
    )
    stuck = await commands.request(
        async_session,
        user=support,
        command="cost_stop.engage",
        target={"scope": "platform"},
        reason="incident",
        idempotency_key="stuck-running-1",
        enqueue=lost,
    )
    row = await async_session.get(OpsCommandModel, stuck.id)
    row.state = commands.RUNNING
    row.started_at = datetime.now(UTC) - timedelta(hours=1)
    await async_session.flush()
    again = Queue()
    swept = await commands.expire_and_lift(
        async_session, now=datetime.now(UTC) + timedelta(minutes=5), enqueue=again
    )
    assert swept["requeued"] == 1 and again.ids == [view.id]
    assert swept["outcome_unknown"] == 1
    assert (
        await commands.get(async_session, stuck.id)
    ).state == commands.OUTCOME_UNKNOWN
    # Still runs exactly once however often it is enqueued.
    assert (await commands.execute(async_session, view.id)).state == commands.SUCCEEDED
    assert await commands.execute(async_session, view.id) is None
