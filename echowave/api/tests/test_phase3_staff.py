"""Phase 3, `staff`: the superadmin side made complete (STAFF.md, "Phase 3").

Each class covers one audit item that was missing or broken, and each test
was written to fail before its fix:

* impersonation answered 500 under local sign-in (what staging and
  production run), and there was no read-only view-as at all;
* a whole workspace could not be suspended;
* the person screen had no plan, credit, flags or recent errors;
* telephony writes left no audit row, and there was no list of numbers;
* staff could open any call's recording with nothing asked of the customer,
  and could not read a transcript even with consent;
* the analytics outbox backlog was not measured while PostHog was unset;
* there was no view of live calls;
* a person's support tickets could not be listed for the person screen;
* and every staff route refuses a customer -- an owner or a plain member.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api import constants
from api.db.models import (
    AdminActionLogModel,
    DataAccessLogModel,
    OrganizationModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.staff import commands, roles
from api.services.staff import workspaces as staff_workspaces
from api.utils.auth import create_jwt_token

FLAGS = (
    "STAFF_CONSOLE_ENABLED",
    "STAFF_ROLES_ENABLED",
    "STAFF_REFUNDS_ENABLED",
    "STAFF_EVALUATIONS_ENABLED",
    "STAFF_INCIDENTS_ENABLED",
    "OPS_CONSOLE_ENABLED",
    "SUPPORT_INBOX_ENABLED",
    "SUPPORT_ACTIONS_ENABLED",
    "SUPPORT_HELP_ENABLED",
    "COST_STOP_ENABLED",
    "OPERATIONAL_QUOTAS_ENABLED",
    "VOICE_LATENCY_ENABLED",
    "VOICE_WATCH_ENABLED",
)


@pytest.fixture
def staff_on(monkeypatch):
    for name in FLAGS:
        monkeypatch.setattr(constants, name, True)
    staff_workspaces.invalidate()
    yield
    staff_workspaces.invalidate()


@pytest.fixture
def local_auth(monkeypatch):
    from api.routes import superuser
    from api.services.auth import depends

    monkeypatch.setattr(depends, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(superuser, "AUTH_PROVIDER", "local")


async def _user(db, session, name: str, tier: str | None = None) -> UserModel:
    run = uuid4().hex[:8]
    user, _ = await db.get_or_create_user_by_provider_id(f"{name}-{run}")
    user.email = f"{name}-{run}@example.test"
    user.staff_role = tier
    session.add(user)
    await session.flush()
    return user


async def _org(db, user, role: str = "owner") -> OrganizationModel:
    org, _ = await db.get_or_create_organization_by_provider_id(
        org_provider_id=f"org-{uuid4().hex[:10]}", user_id=user.id
    )
    await db.add_user_to_organization(user.id, org.id, role=role)
    await db.update_user_selected_organization(user.id, org.id)
    return org


async def _ctx(user) -> roles.StaffContext:
    return roles.StaffContext(user=user, roles=await roles.effective_roles(user))


def _key() -> str:
    return f"test-{uuid4().hex}"


def _bearer(user: UserModel) -> dict[str, str]:
    return {"authorization": f"Bearer {create_jwt_token(user.id, user.email or '')}"}


def _client() -> AsyncClient:
    from api.app import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _call(session, org: OrganizationModel, user: UserModel, **fields):
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
    fields.setdefault("mode", "plivo")
    fields.setdefault("is_completed", True)
    run = WorkflowRunModel(
        name="call",
        workflow_id=workflow.id,
        usage_info={},
        cost_info={},
        initial_context={},
        gathered_context={},
        annotations={},
        **fields,
    )
    session.add(run)
    await session.flush()
    return run


# --- impersonation under local sign-in, and read-only view-as ---------------------


@pytest.mark.asyncio
class TestViewAsAndImpersonate:
    async def test_local_sign_in_mints_a_read_only_session_that_cannot_write(
        self, db_session, async_session, staff_on, local_auth
    ):
        owner = await _user(db_session, async_session, "imp-owner", "superadmin")
        customer = await _user(db_session, async_session, "imp-customer")
        await _org(db_session, customer)
        async with _client() as client:
            started = await client.post(
                "/api/v1/superuser/impersonate",
                json={"user_id": customer.id, "reason": "ticket 12", "mode": "read_only"},
                headers=_bearer(owner),
            )
            # Before phase 3 this called Stack Auth anyway and answered 500.
            assert started.status_code == 200, started.text
            body = started.json()
            assert body["auth_provider"] == "local" and body["mode"] == "read_only"
            borrowed = {"authorization": f"Bearer {body['access_token']}"}

            me = await client.get("/api/v1/organizations/staff-access", headers=borrowed)
            assert me.status_code == 200
            write = await client.post(
                "/api/v1/organizations/staff-access",
                json={"days": 1},
                headers=borrowed,
            )
            assert write.status_code == 403
            assert "read-only" in write.json()["detail"]
            # The way out still works from a read-only session.
            stop = await client.post("/api/v1/impersonation/stop", headers=borrowed)
            assert stop.status_code == 200 and stop.json()["recorded"] is True
            # And once stopped, the token is refused on the server.
            after = await client.get("/api/v1/organizations/staff-access", headers=borrowed)
            assert after.status_code == 401

        start = (
            await async_session.scalars(
                select(AdminActionLogModel).where(
                    AdminActionLogModel.action == "impersonation_started",
                    AdminActionLogModel.target_user_id == customer.id,
                )
            )
        ).one()
        assert start.actor_user_id == owner.id
        assert start.note.startswith("mode=read_only") and "ticket 12" in start.note

    async def test_a_full_impersonation_can_write_and_staff_can_end_it(
        self, db_session, async_session, staff_on, local_auth
    ):
        owner = await _user(db_session, async_session, "imp2-owner", "superadmin")
        customer = await _user(db_session, async_session, "imp2-customer")
        await _org(db_session, customer)
        async with _client() as client:
            started = await client.post(
                "/api/v1/superuser/impersonate",
                json={"email": customer.email, "reason": "set up for them", "mode": "full"},
                headers=_bearer(owner),
            )
            assert started.status_code == 200, started.text
            borrowed = {"authorization": f"Bearer {started.json()['access_token']}"}
            granted = await client.post(
                "/api/v1/organizations/staff-access", json={"days": 1}, headers=borrowed
            )
            assert granted.status_code == 201

            detail = await client.get(
                f"/api/v1/admin/staff/users/{customer.id}", headers=_bearer(owner)
            )
            assert detail.json()["assisted_access"]["state"] == "full"
            ended = await client.post(
                f"/api/v1/admin/staff/users/{customer.id}/assisted-access/end",
                headers=_bearer(owner),
            )
            assert ended.json()["ended"] is True
            refused = await client.get("/api/v1/organizations/staff-access", headers=borrowed)
            assert refused.status_code == 401
            detail = await client.get(
                f"/api/v1/admin/staff/users/{customer.id}", headers=_bearer(owner)
            )
            assert detail.json()["assisted_access"] == {"state": "off"}

    async def test_a_reason_is_required_and_staff_cannot_be_borrowed(
        self, db_session, async_session, staff_on, local_auth
    ):
        owner = await _user(db_session, async_session, "imp3-owner", "superadmin")
        agent = await _user(db_session, async_session, "imp3-support", "support")
        customer = await _user(db_session, async_session, "imp3-customer")
        async with _client() as client:
            no_reason = await client.post(
                "/api/v1/superuser/impersonate",
                json={"user_id": customer.id},
                headers=_bearer(owner),
            )
            assert no_reason.status_code == 422
            staff_target = await client.post(
                "/api/v1/superuser/impersonate",
                json={"user_id": agent.id, "reason": "curious"},
                headers=_bearer(owner),
            )
            assert staff_target.status_code == 403
            by_support = await client.post(
                "/api/v1/superuser/impersonate",
                json={"user_id": customer.id, "reason": "curious"},
                headers=_bearer(agent),
            )
            assert by_support.status_code == 403

    async def test_a_read_only_session_cannot_open_a_conversation(self):
        from fastapi import HTTPException

        from api.services.auth import impersonation_tokens

        with pytest.raises(HTTPException) as refused:
            impersonation_tokens.refuse_write(
                {"imp": 1, "imp_mode": "read_only"}, "WEBSOCKET", None
            )
        assert refused.value.status_code == 403
        impersonation_tokens.refuse_write({"imp": 1, "imp_mode": "full"}, "WEBSOCKET", None)

    async def test_read_only_is_refused_under_stack_auth(
        self, db_session, async_session, staff_on, monkeypatch
    ):
        from api.routes import superuser

        monkeypatch.setattr(superuser, "AUTH_PROVIDER", "stack")
        owner = await _user(db_session, async_session, "imp4-owner", "superadmin")
        from fastapi import HTTPException

        from api.routes.superuser import ImpersonateRequest

        class _Http:
            client = None

        with pytest.raises(HTTPException) as refused:
            await superuser.impersonate(
                ImpersonateRequest(user_id=1, reason="x-ray", mode="read_only"),
                _Http(),
                owner,
            )
        assert refused.value.status_code == 409


# --- workspace suspension ----------------------------------------------------------


@pytest.mark.asyncio
class TestWorkspaceSuspension:
    async def test_two_people_suspend_a_workspace_and_it_holds(
        self, db_session, async_session, staff_on
    ):
        agent = await _user(db_session, async_session, "ws-support", "support")
        owner = await _user(db_session, async_session, "ws-owner", "superadmin")
        customer = await _user(db_session, async_session, "ws-customer")
        member = await _user(db_session, async_session, "ws-member")
        org = await _org(db_session, customer)
        await db_session.add_user_to_organization(member.id, org.id, role="member")
        await db_session.update_user_selected_organization(member.id, org.id)

        view = await commands.request(
            async_session,
            ctx=await _ctx(agent),
            command="workspace.suspend",
            target={"organization_id": org.id},
            reason="Fraud report 9",
            idempotency_key=_key(),
        )
        assert view.state == commands.AWAITING_APPROVAL
        assert view.preview["members"] == 2
        approved = await commands.approve(
            async_session, ctx=await _ctx(owner), command_id=view.id
        )
        assert approved.state == commands.SUCCEEDED
        staff_workspaces.invalidate()

        async with _client() as client:
            for person in (customer, member):
                refused = await client.get(
                    "/api/v1/organizations/staff-access", headers=_bearer(person)
                )
                assert refused.status_code == 403
                assert "workspace is suspended" in refused.json()["detail"]
            # Staff are never refused.
            fine = await client.get("/api/v1/admin/staff/me", headers=_bearer(owner))
            assert fine.status_code == 200

        from api.services import quota_service

        workflow = WorkflowModel(
            name="w",
            user_id=customer.id,
            organization_id=org.id,
            workflow_definition={},
            template_context_variables={},
            call_disposition_codes={},
        )
        async_session.add(workflow)
        await async_session.flush()
        verdict = await quota_service.authorize_workflow_run_start(
            workflow_id=workflow.id, organization_id=org.id
        )
        assert verdict.has_quota is False
        assert verdict.error_code == "workspace_suspended"

        restore = await commands.request(
            async_session,
            ctx=await _ctx(agent),
            command="workspace.unsuspend",
            target={"organization_id": org.id},
            reason="Cleared",
            idempotency_key=_key(),
        )
        assert restore.state == commands.SUCCEEDED
        staff_workspaces.invalidate()
        async with _client() as client:
            back = await client.get(
                "/api/v1/organizations/staff-access", headers=_bearer(customer)
            )
            assert back.status_code == 200

    async def test_off_with_the_console(self, db_session, async_session, staff_on, monkeypatch):
        customer = await _user(db_session, async_session, "ws2-customer")
        org = await _org(db_session, customer)
        org_row = await async_session.get(OrganizationModel, org.id)
        org_row.staff_suspended_at = datetime.now(UTC)
        await async_session.flush()
        staff_workspaces.invalidate()
        assert await staff_workspaces.is_suspended(org.id) is True
        monkeypatch.setattr(constants, "STAFF_CONSOLE_ENABLED", False)
        assert await staff_workspaces.is_suspended(org.id) is False


# --- the person screen ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_detail_shows_plan_credit_flags_and_errors(
    db_session, async_session, staff_on
):
    from api.db.models import AgentEventModel, CreditLedgerModel
    from api.db.feature_override_models import FeatureOverrideModel

    agent = await _user(db_session, async_session, "pd-support", "support")
    customer = await _user(db_session, async_session, "pd-customer")
    org = await _org(db_session, customer)
    async_session.add_all(
        [
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=50000,
                kind="adjustment",
                balance_after_paise=50000,
            ),
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=-1200,
                kind="debit",
                balance_after_paise=48800,
            ),
            AgentEventModel(
                organization_id=org.id,
                kind="could_not",
                actor="agent",
                summary="Could not reach Ravi's private number 98400 12345",
                at=datetime.now(UTC),
            ),
            FeatureOverrideModel(
                feature="people", organization_id=org.id, enabled=True, set_by_user_id=agent.id
            ),
        ]
    )
    await async_session.flush()
    from api.services import features

    await features.refresh_overrides()
    async with _client() as client:
        response = await client.get(
            f"/api/v1/admin/staff/users/{customer.id}", headers=_bearer(agent)
        )
    assert response.status_code == 200, response.text
    summary = response.json()["workspaces"][0]["summary"]
    assert summary["plan"]
    from sqlalchemy import func

    ledger = await async_session.scalar(
        select(func.sum(CreditLedgerModel.delta_paise)).where(
            CreditLedgerModel.organization_id == org.id
        )
    )
    assert summary["balance_paise"] == int(ledger)  # whatever signup granted, plus ours
    assert summary["spent_paise_28d"] == 1200
    assert len(summary["recent_errors"]) == 1
    assert any(f["name"] == "people" and f["source"] == "override" for f in summary["flags"])
    # The failure's words never reach the console.
    assert "Ravi" not in response.text and "98400" not in response.text
    features.clear_snapshot()


# --- telephony ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_numbers_are_listed_and_lending_one_is_audited(
    db_session, async_session, staff_on
):
    owner = await _user(db_session, async_session, "tel-owner", "superadmin")
    customer = await _user(db_session, async_session, "tel-customer")
    org = await _org(db_session, customer)
    config = TelephonyConfigurationModel(
        organization_id=org.id, name="Main", provider="plivo", credentials={}
    )
    async_session.add(config)
    await async_session.flush()
    number = TelephonyPhoneNumberModel(
        organization_id=org.id,
        telephony_configuration_id=config.id,
        address="+918041234567",
        address_normalized="+918041234567",
        address_type="pstn",
    )
    async_session.add(number)
    await async_session.flush()
    async with _client() as client:
        listed = await client.get(
            "/api/v1/admin/telephony/phone-numbers",
            params={"q": "8041"},
            headers=_bearer(owner),
        )
        assert listed.status_code == 200, listed.text
        rows = listed.json()["numbers"]
        assert [r["id"] for r in rows] == [number.id]
        assert rows[0]["provider"] == "plivo" and rows[0]["organization_id"] == org.id
        lent = await client.post(
            f"/api/v1/admin/telephony/phone-numbers/{number.id}/shared-outbound",
            json={"shared": True},
            headers=_bearer(owner),
        )
        assert lent.status_code == 200, lent.text
    row = (
        await async_session.scalars(
            select(AdminActionLogModel).where(
                AdminActionLogModel.action == "telephony_shared_outbound"
            )
        )
    ).one()
    assert row.actor_user_id == owner.id and row.target_organization_id == org.id


# --- call content under consent ----------------------------------------------------------


@pytest.mark.asyncio
class TestCallContent:
    async def test_no_recording_or_transcript_without_the_workspaces_consent(
        self, db_session, async_session, staff_on
    ):
        owner = await _user(db_session, async_session, "cc-owner", "superadmin")
        customer = await _user(db_session, async_session, "cc-customer")
        member = await _user(db_session, async_session, "cc-member")
        org = await _org(db_session, customer)
        await db_session.add_user_to_organization(member.id, org.id, role="member")
        await db_session.update_user_selected_organization(member.id, org.id)
        run = await _call(
            async_session,
            org,
            customer,
            recording_url="recordings/1.wav",
            logs={
                "realtime_feedback_events": [
                    {"type": "rtf-user-transcription", "payload": {"text": "Is my order out?", "final": True, "timestamp": "00:01"}},
                    {"type": "rtf-bot-text", "payload": {"text": "It left this morning.", "timestamp": "00:03"}},
                ]
            },
        )
        async with _client() as client:
            detail = await client.get(
                f"/api/v1/admin/billing/calls/{run.id}", headers=_bearer(owner)
            )
            assert detail.status_code == 200, detail.text
            # Before phase 3 the recording's address came back unconditionally.
            assert detail.json()["recording_url"] is None
            assert detail.json()["has_recording"] is True
            assert detail.json()["content_access"]["state"] == "consent_required"
            transcript = await client.get(
                f"/api/v1/admin/billing/calls/{run.id}/transcript", headers=_bearer(owner)
            )
            assert transcript.status_code == 403
            runs = await client.get(
                "/api/v1/superuser/workflow-runs", headers=_bearer(owner)
            )
            mine = [r for r in runs.json()["workflow_runs"] if r["id"] == run.id]
            assert mine and mine[0]["recording_url"] is None
            assert mine[0]["content_access"] == "consent_required"

            # A plain member cannot give it; an owner can.
            by_member = await client.post(
                "/api/v1/organizations/staff-access",
                json={"workflow_run_id": run.id, "days": 1},
                headers=_bearer(member),
            )
            assert by_member.status_code == 403
            grant = await client.post(
                "/api/v1/organizations/staff-access",
                json={"workflow_run_id": run.id, "days": 1, "reason": "ticket 3"},
                headers=_bearer(customer),
            )
            assert grant.status_code == 201, grant.text

            detail = await client.get(
                f"/api/v1/admin/billing/calls/{run.id}", headers=_bearer(owner)
            )
            assert detail.json()["recording_url"] == "recordings/1.wav"
            transcript = await client.get(
                f"/api/v1/admin/billing/calls/{run.id}/transcript", headers=_bearer(owner)
            )
            assert transcript.status_code == 200
            assert [t["role"] for t in transcript.json()["turns"]] == ["caller", "agent"]

            revoke = await client.delete(
                f"/api/v1/organizations/staff-access/{grant.json()['id']}",
                headers=_bearer(customer),
            )
            assert revoke.status_code == 200
            again = await client.get(
                f"/api/v1/admin/billing/calls/{run.id}/transcript", headers=_bearer(owner)
            )
            assert again.status_code == 403

        reads = (
            await async_session.scalars(
                select(DataAccessLogModel).where(
                    DataAccessLogModel.workflow_run_id == run.id,
                    DataAccessLogModel.actor_kind == "staff",
                )
            )
        ).all()
        assert {r.resource_type for r in reads} == {"recording", "transcript"}
        assert all(r.user_id == owner.id for r in reads)

    async def test_a_grant_cannot_name_another_workspaces_call(
        self, db_session, async_session, staff_on
    ):
        a = await _user(db_session, async_session, "cc2-a")
        b = await _user(db_session, async_session, "cc2-b")
        await _org(db_session, a)
        org_b = await _org(db_session, b)
        run_b = await _call(async_session, org_b, b)
        async with _client() as client:
            refused = await client.post(
                "/api/v1/organizations/staff-access",
                json={"workflow_run_id": run_b.id, "days": 1},
                headers=_bearer(a),
            )
        assert refused.status_code == 404


# --- operations ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_outbox_backlog_is_measured_while_posthog_is_unset(
    db_session, async_session, monkeypatch
):
    from api.db.controls_models import AnalyticsOutboxModel
    from api.services.events import outbox
    from api.services.ops import infra_health

    monkeypatch.setattr(outbox, "enabled", lambda: True)
    monkeypatch.setattr(constants, "POSTHOG_API_KEY", None)
    async_session.add(
        AnalyticsOutboxModel(
            event_id=str(uuid4()),
            name="task_completed",
            envelope={},
            occurred_at=datetime.now(UTC),
        )
    )
    await async_session.flush()
    signal = await infra_health._analytics()
    assert signal.status == infra_health.NOT_CONFIGURED
    # Before phase 3: no metrics at all, so a growing backlog was invisible.
    assert signal.metrics["pending"] >= 1
    assert "held" in signal.detail


@pytest.mark.asyncio
async def test_active_calls_show_live_and_stuck(db_session, async_session):
    from api.services.staff import operations

    customer = await _user(db_session, async_session, "ac-customer")
    org = await _org(db_session, customer)
    now = datetime.now(UTC)
    await _call(async_session, org, customer, state="running", is_completed=False, created_at=now - timedelta(minutes=2))
    stuck = await _call(async_session, org, customer, state="running", is_completed=False, created_at=now - timedelta(minutes=45))
    never = await _call(async_session, org, customer, state="initialized", is_completed=False, created_at=now - timedelta(minutes=10))
    await _call(async_session, org, customer, mode="textchat", state="running", is_completed=False, created_at=now - timedelta(minutes=50))
    found = await operations.active_calls(async_session, now=now)
    ours = {c["workflow_run_id"]: c["reason"] for c in found["possibly_stuck"]}
    assert ours.get(stuck.id) == "long_running"
    assert ours.get(never.id) == "never_connected"
    assert found["live"] >= 2
    assert all(c["mode"] != "textchat" for c in found["possibly_stuck"])


@pytest.mark.asyncio
async def test_support_queue_narrows_to_one_person(db_session, async_session, staff_on):
    from api.services.support import tickets

    customer = await _user(db_session, async_session, "sq-customer")
    other = await _user(db_session, async_session, "sq-other")
    org = await _org(db_session, customer)
    org2 = await _org(db_session, other)
    from api.db.support_models import SupportTicketModel

    async_session.add_all(
        [
            SupportTicketModel(organization_id=org.id, requester_user_id=customer.id, category="other", subject="Mine", shared={}),
            SupportTicketModel(organization_id=org2.id, requester_user_id=other.id, category="other", subject="Theirs", shared={}),
        ]
    )
    await async_session.flush()
    rows = await tickets.queue(staff_id=1, status=None, requester_user_id=customer.id)
    assert [r["subject"] for r in rows] == ["Mine"]


# --- every staff route refuses a customer --------------------------------------------


def _staff_routes():
    from fastapi.routing import APIRoute

    from api.app import app

    out = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not (
            route.path.startswith("/api/v1/admin")
            or route.path.startswith("/api/v1/superuser")
        ):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((method, route.path))
    return out


def _concrete(path: str) -> str:
    import re

    return re.sub(r"\{[^}]+\}", "1", path)


@pytest.mark.asyncio
async def test_every_staff_route_refuses_an_owner_and_a_member(
    db_session, async_session, staff_on, monkeypatch
):
    import api.middleware_rate_limit as rate_limit

    # Hundreds of requests from one address in a second: the limiter would
    # answer 429, which proves nothing about the staff gate.
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_ENABLED", False)
    owner_a = await _user(db_session, async_session, "sweep-owner-a")
    org = await _org(db_session, owner_a)
    member_b = await _user(db_session, async_session, "sweep-member-b")
    await db_session.add_user_to_organization(member_b.id, org.id, role="member")
    await db_session.update_user_selected_organization(member_b.id, org.id)

    routes = _staff_routes()
    assert len(routes) > 150  # the whole surface, not a sample
    leaks = []
    async with _client() as client:
        for person in (owner_a, member_b):
            for method, path in routes:
                response = await client.request(
                    method, _concrete(path), headers=_bearer(person), json={}
                )
                if response.status_code not in (401, 403, 404):
                    leaks.append((person.email, method, path, response.status_code))
    assert leaks == []


class _ExpireOnCommit:
    """The test session keeps objects alive across commit; production's
    (expire_on_commit=True) does not. This behaves like production: after
    commit every object is expired, so touching one is an IO error."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s, name)

    async def commit(self):
        await self._s.flush()
        self._s.expire_all()


@pytest.mark.asyncio
async def test_grants_and_transcripts_survive_a_production_commit(
    db_session, async_session, staff_on
):
    from api.services.staff import call_content

    owner = await _user(db_session, async_session, "exp-owner", "superadmin")
    customer = await _user(db_session, async_session, "exp-customer")
    org = await _org(db_session, customer)
    run = await _call(async_session, org, customer, logs={"realtime_feedback_events": []})
    run_id, org_id, owner_id, customer_id = run.id, org.id, owner.id, customer.id
    session = _ExpireOnCommit(async_session)
    granted = await call_content.grant(
        session, organization_id=org_id, user_id=customer_id, workflow_run_id=run_id, days=1, reason=None
    )
    assert granted["scope"] == "call"
    read = await call_content.read_transcript(
        session, workflow_run_id=run_id, staff_user_id=owner_id, ip_address=None
    )
    assert read["state"] == "empty"
    revoked = await call_content.revoke(
        session, organization_id=org_id, user_id=customer_id, grant_id=granted["id"]
    )
    assert revoked["revoked_at"] is not None
