"""The staff audit viewer, system strip and audited impersonation stop
(ADMIN-2: A4, A6, A7)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from api.db import admin_audit_client
from api.db.models import (
    AdminActionLogModel,
    BillingAuditLogModel,
    OrganizationModel,
    UserModel,
)
from api.services import system_status
from api.services.auth import impersonation_audit

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


async def _org(session, slug):
    org = OrganizationModel(provider_id=f"org-audit-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _user(session, slug, *, staff_role=None):
    user = UserModel(
        provider_id=f"user-audit-{slug}",
        email=f"{slug}@audit.example",
        staff_role=staff_role,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed(session):
    """Two accounts, interleaved rows from both logs."""
    staff = await _user(session, "staff", staff_role="superadmin")
    a = await _org(session, "a")
    b = await _org(session, "b")
    session.add_all(
        [
            AdminActionLogModel(
                actor_user_id=staff.id,
                action="trial_end_set",
                target_organization_id=a.id,
                note="a-1",
                created_at=T0,
            ),
            BillingAuditLogModel(
                organization_id=a.id,
                actor_user_id=staff.id,
                action="credit_adjusted",
                old_value={"balance": 0},
                new_value={"balance": 100},
                note="a-2",
                created_at=T0 + timedelta(minutes=1),
            ),
            AdminActionLogModel(
                actor_user_id=staff.id,
                action="impersonation_started",
                target_organization_id=b.id,
                note="b-1",
                created_at=T0 + timedelta(minutes=2),
            ),
            AdminActionLogModel(
                actor_user_id=staff.id,
                action="trial_end_set",
                target_organization_id=a.id,
                note="a-3",
                created_at=T0 + timedelta(minutes=3),
            ),
            # Same instant as a-3, other table: the cursor must not lose it.
            BillingAuditLogModel(
                organization_id=a.id,
                actor_user_id=staff.id,
                action="credit_adjusted",
                note="a-4",
                created_at=T0 + timedelta(minutes=3),
            ),
        ]
    )
    await session.flush()
    return staff, a, b


@pytest.mark.asyncio
class TestTheAuditStream:
    async def test_both_logs_newest_first_for_one_account(self, async_session):
        _staff, a, _b = await _seed(async_session)

        page = await admin_audit_client.audit_page(async_session, organization_id=a.id)

        notes = [e["note"] for e in page["entries"]]
        assert notes[0] in {"a-3", "a-4"} and notes[1] in {"a-3", "a-4"}
        assert notes[2:] == ["a-2", "a-1"]
        assert {e["source"] for e in page["entries"]} == {"admin", "billing"}
        assert all(e["organization_id"] == a.id for e in page["entries"])
        assert page["entries"][0]["actor_email"] == "staff@audit.example"
        assert page["next_before"] is None

    async def test_the_cursor_walks_every_row_once(self, async_session):
        staff, _a, _b = await _seed(async_session)

        seen = []
        before = None
        for _ in range(10):
            page = await admin_audit_client.audit_page(
                async_session,
                actor_user_id=staff.id,
                before=admin_audit_client.parse_cursor(before),
                limit=2,
            )
            seen += [e["key"] for e in page["entries"]]
            before = page["next_before"]
            if before is None:
                break

        assert len(seen) == 5
        assert len(set(seen)) == 5

    async def test_filters_by_action(self, async_session):
        staff, _a, _b = await _seed(async_session)
        page = await admin_audit_client.audit_page(
            async_session, actor_user_id=staff.id, action="impersonation_started"
        )
        assert [e["note"] for e in page["entries"]] == ["b-1"]

    async def test_a_bare_timestamp_starts_below_it(self, async_session):
        _staff, a, _b = await _seed(async_session)
        page = await admin_audit_client.audit_page(
            async_session,
            organization_id=a.id,
            before=admin_audit_client.parse_cursor(
                (T0 + timedelta(minutes=2)).isoformat()
            ),
        )
        assert [e["note"] for e in page["entries"]] == ["a-2", "a-1"]

    async def test_a_malformed_cursor_is_refused(self):
        with pytest.raises(ValueError):
            admin_audit_client.parse_cursor("2026-09-01T00:00:00+00:00~nope~1")
        with pytest.raises(ValueError):
            admin_audit_client.parse_cursor("not a date")


@pytest.fixture
def signed_in(monkeypatch, test_client_factory):
    """A client for ``user`` that reaches the real staff gate: the gate calls
    ``get_user`` directly rather than through ``Depends``, so the factory's
    override alone would not reach it."""
    from api.services.auth import depends

    def _for(user):
        async def fake_get_user(*_args, **_kwargs):
            return user

        monkeypatch.setattr(depends, "get_user", fake_get_user)
        return test_client_factory(user)

    return _for


@pytest.mark.asyncio
class TestTheRoutesAreStaffOnly:
    @pytest.mark.parametrize("path", ["/api/v1/admin/audit", "/api/v1/admin/system"])
    async def test_a_customer_is_refused(self, signed_in, async_session, path):
        customer = await _user(async_session, "audit-customer")
        async with signed_in(customer) as client:
            response = await client.get(path)
        assert response.status_code == 403

    async def test_a_superadmin_reads_the_log(self, signed_in, async_session):
        staff, _a, b = await _seed(async_session)
        async with signed_in(staff) as client:
            response = await client.get(
                "/api/v1/admin/audit", params={"organization_id": b.id}
            )
            bad = await client.get("/api/v1/admin/audit", params={"before": "x~y"})
        assert response.status_code == 200
        body = response.json()
        assert [e["note"] for e in body["entries"]] == ["b-1"]
        assert "trial_end_set" in body["actions"]
        assert bad.status_code == 400

    async def test_a_superadmin_reads_the_system_strip(
        self, signed_in, async_session, monkeypatch
    ):
        staff = await _user(async_session, "sys-staff", staff_role="superadmin")

        async def fake_snapshot():
            return {"status": "ok"}

        monkeypatch.setattr(system_status, "snapshot", fake_snapshot)
        async with signed_in(staff) as client:
            response = await client.get("/api/v1/admin/system")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
class TestTheSystemProbes:
    async def test_every_probe_reports_and_a_dead_worker_degrades(self, monkeypatch):
        async def ok():
            return {}

        async def queue():
            return {"length": 4}

        async def worker():
            return {"ok": False, "alive": False, "detail": "stopped"}

        async def balances():
            return {"ok": True, "needs_attention": 0, "providers": []}

        monkeypatch.setattr(system_status, "_database", ok)
        monkeypatch.setattr(system_status, "_redis", ok)
        monkeypatch.setattr(system_status, "_queue", queue)
        monkeypatch.setattr(system_status, "_worker", worker)
        monkeypatch.setattr(system_status, "_balances", balances)
        monkeypatch.setenv("GIT_SHA", "abc1234")

        snap = await system_status.snapshot()

        assert snap["status"] == "degraded"
        assert snap["database"]["ok"] is True
        assert snap["database"]["latency_ms"] is not None
        assert snap["queue"]["length"] == 4
        assert snap["worker"]["alive"] is False
        assert snap["build"]["git_sha"] == "abc1234"

    async def test_a_hanging_probe_is_cut_off_and_a_failing_one_is_named(
        self, monkeypatch
    ):
        monkeypatch.setattr(system_status, "PROBE_TIMEOUT_SECONDS", 0.05)

        async def hang():
            await asyncio.sleep(5)
            return {}

        async def boom():
            raise ConnectionError("redis://secret@host")

        hung = await system_status._probe("database", hang)
        failed = await system_status._probe("redis", boom)

        assert hung["ok"] is False and "No answer" in hung["detail"]
        assert failed["ok"] is False
        assert "ConnectionError" in failed["detail"]
        assert "secret" not in failed["detail"]

    async def test_an_unset_sha_reads_unknown(self, monkeypatch):
        monkeypatch.delenv("GIT_SHA", raising=False)
        monkeypatch.delenv("APP_VERSION", raising=False)
        info = system_status.build_info()
        assert info["git_sha"] == "unknown"
        assert info["version"]


@pytest.mark.asyncio
class TestTheImpersonationStop:
    async def _start(self, session, staff, target, *, at):
        session.add(
            AdminActionLogModel(
                actor_user_id=staff.id,
                action="impersonation_started",
                target_user_id=target.id,
                target_provider_id=target.provider_id,
                target_organization_id=None,
                created_at=at,
            )
        )
        await session.flush()

    async def _stops(self, session, target):
        from sqlalchemy import select

        return (
            await session.scalars(
                select(AdminActionLogModel).where(
                    AdminActionLogModel.action == "impersonation_stopped",
                    AdminActionLogModel.target_user_id == target.id,
                )
            )
        ).all()

    async def test_the_stop_is_written_for_the_staffer_once(self, async_session):
        staff = await _user(async_session, "imp-staff", staff_role="superadmin")
        customer = await _user(async_session, "imp-customer")
        now = datetime.now(UTC)
        await self._start(
            async_session, staff, customer, at=now - timedelta(minutes=12)
        )

        first = await impersonation_audit.record_stop(
            async_session,
            user_id=customer.id,
            provider_id=customer.provider_id,
            actor_ip="10.0.0.1",
            now=now,
        )
        second = await impersonation_audit.record_stop(
            async_session,
            user_id=customer.id,
            provider_id=customer.provider_id,
            actor_ip="10.0.0.1",
            now=now + timedelta(seconds=5),
        )

        assert first["recorded"] is True and first["minutes"] == 12
        assert second == {"recorded": False, "reason": "already_stopped"}
        rows = await self._stops(async_session, customer)
        assert len(rows) == 1
        # Attributed to the staffer who started it, never the caller.
        assert rows[0].actor_user_id == staff.id
        assert rows[0].actor_ip == "10.0.0.1"

    async def test_nothing_is_written_without_an_open_impersonation(
        self, async_session
    ):
        staff = await _user(async_session, "imp-staff-2", staff_role="superadmin")
        customer = await _user(async_session, "imp-customer-2")
        bystander = await _user(async_session, "imp-bystander")
        now = datetime.now(UTC)
        # Started over an hour ago: the borrowed session has already expired.
        await self._start(async_session, staff, customer, at=now - timedelta(hours=2))
        # And a live one on somebody else.
        await self._start(
            async_session, staff, bystander, at=now - timedelta(minutes=1)
        )

        result = await impersonation_audit.record_stop(
            async_session,
            user_id=customer.id,
            provider_id=customer.provider_id,
            actor_ip=None,
            now=now,
        )

        assert result == {"recorded": False, "reason": "no_open_impersonation"}
        assert await self._stops(async_session, customer) == []
        assert await self._stops(async_session, bystander) == []

    async def test_the_route_records_the_stop_for_the_borrowed_session(
        self, test_client_factory, db_session, async_session
    ):
        staff = await _user(async_session, "imp-staff-3", staff_role="superadmin")
        customer = await _user(async_session, "imp-customer-3")
        await self._start(
            async_session,
            staff,
            customer,
            at=datetime.now(UTC) - timedelta(minutes=3),
        )

        async with test_client_factory(customer) as client:
            response = await client.post("/api/v1/impersonation/stop")

        assert response.status_code == 200
        assert response.json()["recorded"] is True
        rows = await self._stops(async_session, customer)
        assert [r.actor_user_id for r in rows] == [staff.id]
