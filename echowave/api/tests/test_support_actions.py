"""Typed support actions (launch stream `support`, screen 33; handoff 33).

Done when: staff can preview a typed command with its target, environment,
old and new values, impact and approvers; request it once however often the
button is pressed; a second person (at the command's tier) approves the
exact version; it runs once on the worker and the customer's ticket says
what happened; changed parameters or a moved target need a fresh approval;
an unexpected failure is "outcome unknown" until reconciled; everything is
audited -- and no free-form command, shell or SQL is reachable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api import constants
from api.db import db_client
from api.services import quotas
from api.services.support import actions, commands, tickets
from api.services.workflow import task_ledger
from api.tests.support.help_desk import (
    cleanup,
    client_as,
    make_setting,
    person,
    staff,
    switch_on,
)


@pytest.fixture
async def desk(test_engine):
    ids = await make_setting()
    ticket, _ = await tickets.create(
        organization_id=ids.org_a,
        user_id=ids.customer,
        category="usage_limits",
        description="I ran out of messages for today.",
    )
    ids.ticket = ticket["id"]
    yield ids
    await cleanup(ids)


@pytest.fixture
def on(monkeypatch):
    switch_on(monkeypatch)
    monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
    monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
    monkeypatch.setattr(constants, "MEMBER_PREFERENCES_ENABLED", True)


@pytest.fixture
def enqueue():
    with patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as mock:
        yield mock


def _grant(desk, **params):
    return {
        "kind": "grant_usage",
        "organization_id": desk.org_a,
        "target_user_id": desk.customer,
        "ticket_id": desk.ticket,
        "params": {"allowance": "model_turns", "extra": 20, "days": 3, **params},
    }


async def _requested(desk, body=None, key="req-1", who=None):
    async with client_as(who or staff(desk.agent)) as client:
        response = await client.post(
            "/api/v1/admin/support/actions",
            json={
                **(body or _grant(desk)),
                "reason": "Customer asked on the ticket for more.",
            },
            headers={"Idempotency-Key": key},
        )
    assert response.status_code in (200, 201), response.text
    return response.json()["action"]


async def _audit_actions(action_id: int) -> list[str]:
    async with db_client.async_session() as session:
        rows = await session.execute(
            text("SELECT action FROM admin_action_log WHERE note LIKE :n ORDER BY id"),
            {"n": f"%action={action_id};%"},
        )
        return [r[0] for r in rows.all()]


@pytest.mark.asyncio
class TestCatalogue:
    async def test_off_is_not_there(self, desk):
        async with client_as(staff(desk.agent)) as client:
            assert (
                await client.get("/api/v1/admin/support/actions/commands")
            ).status_code == 404

    async def test_customers_cannot_reach_it(self, desk, on):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            assert (
                await client.get("/api/v1/admin/support/actions/commands")
            ).status_code == 403
            assert (
                await client.post(
                    "/api/v1/admin/support/actions/preview", json=_grant(desk)
                )
            ).status_code == 403

    async def test_every_command_says_whether_it_can_run_and_why_not(
        self, desk, on, monkeypatch
    ):
        async with client_as(staff(desk.agent)) as client:
            rows = (
                await client.get(
                    "/api/v1/admin/support/actions/commands",
                    params={"organization_id": desk.org_a},
                )
            ).json()["commands"]
        by_kind = {r["kind"]: r for r in rows}
        # What must appear: the five runnable commands and the four that are not.
        assert {k for k, r in by_kind.items() if r["state"] == "available"} == {
            "grant_usage",
            "cancel_task",
            "retry_task",
            "pause_routine",
            "change_preference",
        }
        for kind in (
            "refund_payment",
            "export_data",
            "delete_data",
            "repair_connection",
        ):
            assert by_kind[kind]["state"] == "unavailable" and by_kind[kind]["reason"]
        assert by_kind["grant_usage"]["approver_role"] == "superadmin"
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", False)
        state, reason = commands.COMMANDS["grant_usage"].state_for(desk.org_a)
        assert state == "needs_setup" and "operational_quotas" in reason

    async def test_no_free_form_command_exists(self, desk, on):
        async with client_as(staff(desk.agent)) as client:
            for kind in ("shell", "sql", "aws", "run_command"):
                response = await client.post(
                    "/api/v1/admin/support/actions/preview",
                    json={
                        **_grant(desk),
                        "kind": kind,
                        "params": {"command": "rm -rf /"},
                    },
                )
                assert response.status_code == 422, kind
            sneaky = await client.post(
                "/api/v1/admin/support/actions/preview",
                json=_grant(desk, sql="DROP TABLE users"),
            )
            assert sneaky.status_code == 422
            refund = await client.post(
                "/api/v1/admin/support/actions/preview",
                json={**_grant(desk), "kind": "refund_payment", "params": {}},
            )
            assert refund.status_code == 422 and "finance" in refund.json()["detail"]
        assert not any("command" in str(c.fields) for c in commands.COMMANDS.values())


@pytest.mark.asyncio
class TestPreviewAndRequest:
    async def test_preview_shows_target_old_and_new_impact_and_approvers(
        self, desk, on
    ):
        async with client_as(staff(desk.agent)) as client:
            shown = (
                await client.post(
                    "/api/v1/admin/support/actions/preview", json=_grant(desk)
                )
            ).json()
        base = constants.OPERATIONAL_QUOTA_MODEL_TURNS
        assert shown["changes"] == [
            {"field": "Daily messages", "old": base, "new": base + 20}
        ]
        assert shown["impact"].startswith("Up to 60 more messages")
        assert shown["target"] == {
            "organization_id": desk.org_a,
            "user_id": desk.customer,
            "ticket_id": desk.ticket,
        }
        assert shown["environment"] == constants.ENVIRONMENT
        assert "superadmin" in shown["approvers"]
        assert len(shown["version"]) == 32

    async def test_repeat_clicks_make_one_request(self, desk, on):
        first = await _requested(desk, key="same")
        again = await _requested(desk, key="same")
        assert first["id"] == again["id"] and first["state"] == "requested"
        rows = await actions.list_actions(ticket_id=desk.ticket)
        assert len(rows) == 1
        assert await _audit_actions(first["id"]) == ["support_action_requested"]

    async def test_a_preview_that_moved_since_reading_is_refused(self, desk, on):
        async with client_as(staff(desk.agent)) as client:
            response = await client.post(
                "/api/v1/admin/support/actions",
                json={
                    **_grant(desk),
                    "reason": "Customer asked.",
                    "expected_version": "0" * 32,
                },
                headers={"Idempotency-Key": "k"},
            )
        assert response.status_code == 409

    async def test_scope_is_checked(self, desk, on):
        cases = [
            # Not a member of the workspace.
            {**_grant(desk), "target_user_id": desk.stranger},
            # A ticket from another workspace.
            {
                **_grant(desk),
                "organization_id": desk.org_b,
                "target_user_id": desk.customer,
            },
            # The customer's own setting, without their request.
            {
                "kind": "change_preference",
                "organization_id": desk.org_a,
                "target_user_id": desk.customer,
                "ticket_id": None,
                "params": {"field": "timezone", "value": "Asia/Kolkata"},
            },
            # Their request, about somebody else.
            {
                "kind": "change_preference",
                "organization_id": desk.org_a,
                "target_user_id": desk.colleague,
                "ticket_id": desk.ticket,
                "params": {"field": "timezone", "value": "Asia/Kolkata"},
            },
        ]
        async with client_as(staff(desk.agent)) as client:
            for body in cases:
                response = await client.post(
                    "/api/v1/admin/support/actions/preview", json=body
                )
                assert response.status_code == 422, body


@pytest.mark.asyncio
class TestApproval:
    async def test_the_requester_cannot_approve_their_own(self, desk, on):
        action = await _requested(desk, who=staff(desk.boss, "superadmin"))
        async with client_as(staff(desk.boss, "superadmin")) as client:
            response = await client.post(
                f"/api/v1/admin/support/actions/{action['id']}/approve",
                json={"version": action["version"]},
            )
        assert response.status_code == 403
        # And the database refuses it too, whatever the code path.
        async with db_client.async_session() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        "UPDATE support_actions SET approved_by = requested_by WHERE id = :i"
                    ),
                    {"i": action["id"]},
                )
            await session.rollback()

    async def test_a_grant_needs_a_superadmin_second_person(self, desk, on):
        action = await _requested(desk)
        async with client_as(staff(desk.agent2)) as client:
            refused = await client.post(
                f"/api/v1/admin/support/actions/{action['id']}/approve",
                json={"version": action["version"]},
            )
        assert refused.status_code == 403
        async with client_as(staff(desk.boss, "superadmin")) as client:
            wrong = await client.post(
                f"/api/v1/admin/support/actions/{action['id']}/approve",
                json={"version": "f" * 32},
            )
            approved = await client.post(
                f"/api/v1/admin/support/actions/{action['id']}/approve",
                json={"version": action["version"]},
            )
        assert wrong.status_code == 409
        assert approved.status_code == 200 and approved.json()["state"] == "approved"
        assert approved.json()["approved_version"] == action["version"]

    async def test_changed_parameters_need_approving_again(self, desk, on, enqueue):
        action = await _requested(desk)
        await actions.approve(
            action_id=action["id"],
            staff=staff(desk.boss, "superadmin"),
            version=action["version"],
        )
        async with client_as(staff(desk.agent)) as client:
            revised = (
                await client.post(
                    f"/api/v1/admin/support/actions/{action['id']}/revise",
                    json={
                        "params": {"allowance": "model_turns", "extra": 500, "days": 3}
                    },
                )
            ).json()
            run = await client.post(f"/api/v1/admin/support/actions/{action['id']}/run")
        assert revised["state"] == "requested" and revised["approved_by"] is None
        assert revised["version"] != action["version"]
        assert run.status_code == 409
        enqueue.assert_not_awaited()

    async def test_a_target_that_moved_cannot_be_approved(self, desk, on):
        task, _ = await task_ledger.create(
            organization_id=desk.org_a, title="Chase payment", created_by=desk.customer
        )
        body = {
            "kind": "cancel_task",
            "organization_id": desk.org_a,
            "target_user_id": desk.customer,
            "ticket_id": desk.ticket,
            "params": {"task_id": task.id},
        }
        action = await _requested(desk, body=body)
        await task_ledger.transition(
            organization_id=desk.org_a,
            task_id=task.id,
            to_state=task_ledger.RUNNING,
            expected_version=1,
        )
        with pytest.raises(actions.Changed):
            await actions.approve(
                action_id=action["id"],
                staff=staff(desk.agent2),
                version=action["version"],
            )

    async def test_requests_and_approvals_expire(self, desk, on, enqueue):
        action = await _requested(desk)
        await actions.approve(
            action_id=action["id"],
            staff=staff(desk.boss, "superadmin"),
            version=action["version"],
        )
        async with db_client.async_session() as session:
            await session.execute(
                text("UPDATE support_actions SET expires_at = :t WHERE id = :i"),
                {"t": datetime.now(UTC) - timedelta(minutes=1), "i": action["id"]},
            )
            await session.commit()
        with pytest.raises(actions.ActionError, match="expired"):
            await actions.run(action_id=action["id"], staff=staff(desk.agent))
        assert (await actions.get(action["id"]))["state"] == "expired"
        enqueue.assert_not_awaited()


@pytest.mark.asyncio
class TestRun:
    async def test_runs_once_and_the_customer_is_told(
        self, desk, on, enqueue, monkeypatch
    ):
        monkeypatch.setattr(constants, "EVENT_CATALOGUE_ENABLED", True)
        monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "test-key")
        before = (await quotas.usage(desk.customer, "model_turns")).limit
        action = await _requested(desk)
        await actions.approve(
            action_id=action["id"],
            staff=staff(desk.boss, "superadmin"),
            version=action["version"],
        )
        async with client_as(staff(desk.agent)) as client:
            first = await client.post(
                f"/api/v1/admin/support/actions/{action['id']}/run"
            )
            again = await client.post(
                f"/api/v1/admin/support/actions/{action['id']}/run"
            )
        assert first.status_code == 202 and first.json()["action"]["state"] == "queued"
        assert again.status_code == 200 and again.json()["queued"] is False
        assert enqueue.await_count == 1
        # The worker, twice: one claim.
        assert await actions.execute(action["id"]) == "succeeded"
        assert await actions.execute(action["id"]) is None
        done = await actions.get(action["id"])
        assert (
            done["state"] == "succeeded" and done["result"]["evidence"]["allowance_id"]
        )
        assert (await quotas.usage(desk.customer, "model_turns")).limit == before + 20
        grants = await quotas.grants(desk.customer)
        assert len(grants) == 1 and commands.marker(action["id"]) in grants[0]["reason"]
        mine = await tickets.get_mine(desk.org_a, desk.customer, desk.ticket)
        assert mine["messages"][-1]["author_kind"] == "system"
        assert mine["messages"][-1]["body"].startswith(
            "Done: Extra messages: 20 more a day"
        )
        assert mine["actions"] == [
            {
                "id": action["id"],
                "summary": done["preview"]["customer_summary"],
                "state": "succeeded",
                "finished_at": done["finished_at"],
            }
        ]
        # What the customer may not see about it.
        assert "Customer asked on the ticket" not in str(mine)
        assert await _audit_actions(action["id"]) == [
            "support_action_requested",
            "support_action_approved",
            "support_action_queued",
            "support_action_succeeded",
        ]
        async with db_client.async_session() as session:
            names = (
                (
                    await session.execute(
                        text(
                            "SELECT name FROM analytics_outbox WHERE name LIKE 'support_action_%' "
                            "ORDER BY occurred_at DESC LIMIT 3"
                        )
                    )
                )
                .scalars()
                .all()
            )
            await session.execute(
                text(
                    "DELETE FROM analytics_outbox WHERE name LIKE 'support_%' OR name LIKE 'ticket_%'"
                )
            )
            await session.commit()
        assert set(names) == {
            "support_action_requested",
            "support_action_approved",
            "support_action_executed",
        }

    async def test_a_run_against_a_moved_target_fails_cleanly(self, desk, on, enqueue):
        task, _ = await task_ledger.create(
            organization_id=desk.org_a, title="Chase payment", created_by=desk.customer
        )
        body = {
            "kind": "cancel_task",
            "organization_id": desk.org_a,
            "target_user_id": desk.customer,
            "ticket_id": desk.ticket,
            "params": {"task_id": task.id},
        }
        action = await _requested(desk, body=body)
        await actions.approve(
            action_id=action["id"], staff=staff(desk.agent2), version=action["version"]
        )
        await actions.run(action_id=action["id"], staff=staff(desk.agent))
        await task_ledger.transition(
            organization_id=desk.org_a,
            task_id=task.id,
            to_state=task_ledger.RUNNING,
            expected_version=1,
        )
        assert await actions.execute(action["id"]) == "failed"
        done = await actions.get(action["id"])
        assert "changed since the preview" in done["result"]["summary"]
        fresh = await db_client.get_task(task.id, organization_id=desk.org_a)
        assert task_ledger.state_of(fresh) == task_ledger.RUNNING
        mine = await tickets.get_mine(desk.org_a, desk.customer, desk.ticket)
        assert mine["messages"][-1]["body"].startswith("We could not do this")

    async def test_cancel_retry_pause_and_preference_do_what_they_say(
        self, desk, on, enqueue
    ):
        stuck, _ = await task_ledger.create(
            organization_id=desk.org_a, title="Stuck", created_by=desk.customer
        )
        failed, _ = await task_ledger.create(
            organization_id=desk.org_a,
            title="Failed",
            brief="b",
            created_by=desk.customer,
        )
        await task_ledger.transition(
            organization_id=desk.org_a,
            task_id=failed.id,
            to_state=task_ledger.FAILED,
            expected_version=1,
        )
        routine = await db_client.create_routine(
            organization_id=desk.org_a,
            workflow_id=None,
            name="Morning brief",
            is_active=True,
        )
        bodies = [
            ("cancel_task", {"task_id": stuck.id}),
            ("retry_task", {"task_id": failed.id}),
            ("pause_routine", {"routine_id": routine.id}),
            ("change_preference", {"field": "timezone", "value": "Asia/Kolkata"}),
        ]
        for n, (kind, params) in enumerate(bodies):
            action = await _requested(
                desk,
                body={
                    "kind": kind,
                    "organization_id": desk.org_a,
                    "target_user_id": desk.customer,
                    "ticket_id": desk.ticket,
                    "params": params,
                },
                key=f"k-{n}",
            )
            await actions.approve(
                action_id=action["id"],
                staff=staff(desk.agent2),
                version=action["version"],
            )
            await actions.run(action_id=action["id"], staff=staff(desk.agent))
            assert await actions.execute(action["id"]) == "succeeded", kind
        assert (
            task_ledger.state_of(
                await db_client.get_task(stuck.id, organization_id=desk.org_a)
            )
            == "cancelled"
        )
        async with db_client.async_session() as session:
            retried = await session.scalar(
                text(
                    "SELECT count(*) FROM agent_tasks WHERE organization_id = :o AND idempotency_key LIKE 'support-retry:%'"
                ),
                {"o": desk.org_a},
            )
        assert retried == 1
        assert (
            await db_client.get_routine(routine.id, organization_id=desk.org_a)
        ).is_active is False
        from api.services import member_preferences

        assert (await member_preferences.get(desk.customer))[
            "timezone"
        ] == "Asia/Kolkata"

    async def test_an_unknown_outcome_is_reconciled_not_retried(
        self, desk, on, enqueue
    ):
        action = await _requested(desk)
        await actions.approve(
            action_id=action["id"],
            staff=staff(desk.boss, "superadmin"),
            version=action["version"],
        )
        await actions.run(action_id=action["id"], staff=staff(desk.agent))

        real = commands.COMMANDS["grant_usage"].run

        async def granted_then_lost(ctx):
            await real(ctx)
            raise TimeoutError("connection reset after commit")

        command = commands.COMMANDS["grant_usage"]
        with patch.dict(
            commands.COMMANDS,
            {
                "grant_usage": commands.Command(
                    **{**command.__dict__, "run": granted_then_lost}
                )
            },
        ):
            assert await actions.execute(action["id"]) == "outcome_unknown"
        unknown = await actions.get(action["id"])
        assert unknown["notice"].startswith("We are checking")
        # It is not run again by asking again.
        _, queued = await actions.run(action_id=action["id"], staff=staff(desk.agent))
        assert queued is False
        async with client_as(staff(desk.agent2)) as client:
            settled = (
                await client.post(
                    f"/api/v1/admin/support/actions/{action['id']}/reconcile"
                )
            ).json()
        assert settled["state"] == "succeeded"
        assert settled["result"]["summary"].startswith(
            "Reconciled: the change is in place"
        )
        assert len(await quotas.grants(desk.customer)) == 1

    async def test_a_dead_worker_is_swept_to_unknown(self, desk, on, enqueue):
        action = await _requested(desk)
        await actions.approve(
            action_id=action["id"],
            staff=staff(desk.boss, "superadmin"),
            version=action["version"],
        )
        await actions.run(action_id=action["id"], staff=staff(desk.agent))
        async with db_client.async_session() as session:
            await session.execute(
                text(
                    "UPDATE support_actions SET state = 'running', started_at = :t WHERE id = :i"
                ),
                {"t": datetime.now(UTC) - timedelta(minutes=11), "i": action["id"]},
            )
            await session.commit()
        stale = await _requested(desk, key="old")
        async with db_client.async_session() as session:
            await session.execute(
                text("UPDATE support_actions SET expires_at = :t WHERE id = :i"),
                {"t": datetime.now(UTC) - timedelta(minutes=1), "i": stale["id"]},
            )
            await session.commit()
        result = await actions.sweep()
        assert result["outcome_unknown"] >= 1 and result["expired"] >= 1
        assert (await actions.get(action["id"]))["state"] == "outcome_unknown"
        assert (await actions.get(stale["id"]))["state"] == "expired"
        # Reconciling finds nothing was granted: failed, safe to ask again.
        settled = await actions.reconcile(
            action_id=action["id"], staff=staff(desk.agent)
        )
        assert settled["state"] == "failed"
        assert "safe to request it again" in settled["result"]["summary"]

    async def test_retry_refuses_an_unknown_outcome(self, desk, on):
        task, _ = await task_ledger.create(
            organization_id=desk.org_a, title="Send", created_by=desk.customer
        )
        await task_ledger.transition(
            organization_id=desk.org_a,
            task_id=task.id,
            to_state=task_ledger.RUNNING,
            expected_version=1,
        )
        await task_ledger.transition(
            organization_id=desk.org_a,
            task_id=task.id,
            to_state=task_ledger.OUTCOME_UNKNOWN,
            expected_version=2,
        )
        with pytest.raises(commands.InvalidTarget, match="already happened"):
            await actions.preview(
                kind="retry_task",
                organization_id=desk.org_a,
                target_user_id=desk.customer,
                ticket_id=desk.ticket,
                params={"task_id": task.id},
            )
