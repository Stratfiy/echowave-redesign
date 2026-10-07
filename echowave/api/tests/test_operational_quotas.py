"""Operational quotas: per-person daily limits outside billing (handoff 9,
15 B; launch stream controls).

Done when: a limit holds in free mode, two requests at once cannot both take
the last unit, nothing but a live staff grant raises a limit, the person
reads why in the thread, and one person's usage never spends another's.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.services import quotas


@pytest.fixture
def quotas_on(monkeypatch):
    monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
    monkeypatch.setattr(constants, "OPERATIONAL_QUOTA_MODEL_TURNS", 3)
    monkeypatch.setattr(constants, "OPERATIONAL_QUOTA_OUTBOUND_MESSAGES", 2)
    monkeypatch.setattr(constants, "OPERATIONAL_QUOTA_VOICE_MINUTES", 5)
    monkeypatch.setattr(constants, "OPERATIONAL_QUOTA_BROWSER_MINUTES", 4)


@pytest.fixture
async def people(test_engine):
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"quota-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"quota-b-{run}")
    staff, _ = await db_client.get_or_create_user_by_provider_id(f"quota-s-{run}")
    try:
        yield a, b, staff
    finally:
        async with db_client.async_session() as session:
            for table in ("operational_usage", "quota_allowances"):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE user_id = ANY(:ids)"),
                    {"ids": [a.id, b.id, staff.id]},
                )
            await session.execute(
                text("DELETE FROM admin_action_log WHERE actor_user_id = :s"),
                {"s": staff.id},
            )
            await session.commit()


@pytest.mark.asyncio
class TestTheLimit:
    async def test_off_nothing_is_counted_or_refused(self, people, monkeypatch):
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", False)
        a, _, _ = people
        for _ in range(100):
            assert await quotas.consume(a.id, quotas.MODEL_TURNS) is None
        async with db_client.async_session() as session:
            rows = await session.scalar(
                text("SELECT count(*) FROM operational_usage WHERE user_id = :u"),
                {"u": a.id},
            )
        assert rows == 0

    async def test_the_limit_holds_in_free_mode(self, people, quotas_on, monkeypatch):
        # Free mode bypasses every plan limit; this one is not a plan limit.
        monkeypatch.setattr(constants, "FREE_MODE_ENABLED", True)
        a, _, _ = people
        for expected in (1, 2, 3):
            assert (await quotas.consume(a.id, quotas.MODEL_TURNS)).used == expected
        with pytest.raises(quotas.QuotaExceeded) as caught:
            await quotas.consume(a.id, quotas.MODEL_TURNS)
        assert caught.value.usage.remaining == 0
        assert (await quotas.usage(a.id, quotas.MODEL_TURNS)).used == 3

    async def test_two_at_once_cannot_both_take_the_last_one(self, people, quotas_on):
        a, _, _ = people
        await quotas.consume(a.id, quotas.MODEL_TURNS, 2)

        async def one():
            try:
                await quotas.consume(a.id, quotas.MODEL_TURNS)
                return True
            except quotas.QuotaExceeded:
                return False

        results = await asyncio.gather(*(one() for _ in range(8)))
        assert results.count(True) == 1
        assert (await quotas.usage(a.id, quotas.MODEL_TURNS)).used == 3

    async def test_a_burst_from_nothing_stops_at_the_limit(self, people, quotas_on):
        a, _, _ = people

        async def one():
            try:
                await quotas.consume(a.id, quotas.BROWSER_MINUTES)
                return True
            except quotas.QuotaExceeded:
                return False

        results = await asyncio.gather(*(one() for _ in range(12)))
        assert results.count(True) == 4

    @pytest.mark.parametrize("amount", [0, -1, -50, True, 1.5, "1", 24 * 60 + 1])
    async def test_no_amount_can_refund_or_overflow(self, people, quotas_on, amount):
        a, _, _ = people
        with pytest.raises(ValueError):
            await quotas.consume(a.id, quotas.MODEL_TURNS, amount)

    async def test_one_large_spend_is_refused_whole(self, people, quotas_on):
        a, _, _ = people
        with pytest.raises(quotas.QuotaExceeded):
            await quotas.consume(a.id, quotas.VOICE_MINUTES, 6)
        assert (await quotas.usage(a.id, quotas.VOICE_MINUTES)).used == 0

    async def test_a_finished_session_is_recorded_past_the_limit(
        self, people, quotas_on
    ):
        a, _, _ = people
        await quotas.consume(a.id, quotas.VOICE_MINUTES, 4)
        usage = await quotas.consume(a.id, quotas.VOICE_MINUTES, 3, force=True)
        assert usage.used == 7
        # ...and the next session is refused.
        with pytest.raises(quotas.QuotaExceeded):
            await quotas.check(a.id, quotas.VOICE_MINUTES)

    @pytest.mark.parametrize("who", [None, 0, -3, True, "7"])
    async def test_an_allowance_belongs_to_a_person(self, quotas_on, who):
        with pytest.raises(ValueError):
            await quotas.consume(who, quotas.MODEL_TURNS)

    async def test_an_unknown_kind_is_refused_not_unlimited(self, people, quotas_on):
        a, _, _ = people
        with pytest.raises(ValueError):
            await quotas.consume(a.id, "model_turns_v2")

    async def test_one_persons_use_never_spends_anothers(self, people, quotas_on):
        a, b, _ = people
        await quotas.consume(a.id, quotas.MODEL_TURNS, 3)
        with pytest.raises(quotas.QuotaExceeded):
            await quotas.consume(a.id, quotas.MODEL_TURNS)
        assert (await quotas.consume(b.id, quotas.MODEL_TURNS)).used == 1

    async def test_the_day_is_the_utc_day(self, people, quotas_on):
        a, _, _ = people
        yesterday = datetime.now(UTC) - timedelta(days=1)
        await quotas.consume(a.id, quotas.MODEL_TURNS, 3, now=yesterday)
        assert (await quotas.consume(a.id, quotas.MODEL_TURNS)).used == 1


@pytest.mark.asyncio
class TestStaffGrants:
    async def test_a_live_grant_raises_the_limit_and_is_audited(
        self, people, quotas_on
    ):
        a, _, staff = people
        await quotas.consume(a.id, quotas.MODEL_TURNS, 3)
        await quotas.grant(
            user_id=a.id,
            kind=quotas.MODEL_TURNS,
            extra=2,
            reason="Pilot demo on Friday",
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            granted_by=staff.id,
        )
        assert (await quotas.consume(a.id, quotas.MODEL_TURNS)).limit == 5
        async with db_client.async_session() as session:
            audited = await session.scalar(
                text(
                    "SELECT count(*) FROM admin_action_log WHERE actor_user_id = :s "
                    "AND action = 'quota_allowance_granted' AND target_user_id = :u"
                ),
                {"s": staff.id, "u": a.id},
            )
        assert audited == 1

    async def test_an_expired_grant_adds_nothing(self, people, quotas_on):
        a, _, staff = people
        row = await quotas.grant(
            user_id=a.id,
            kind=quotas.MODEL_TURNS,
            extra=10,
            reason="For an hour only",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            granted_by=staff.id,
        )
        later = datetime.now(UTC) + timedelta(hours=2)
        assert (await quotas.usage(a.id, quotas.MODEL_TURNS, now=later)).limit == 3
        assert (await quotas.usage(a.id, quotas.MODEL_TURNS)).limit == 13
        assert row.expires_at > datetime.now(UTC)

    async def test_a_revoked_grant_adds_nothing(self, people, quotas_on):
        a, _, staff = people
        row = await quotas.grant(
            user_id=a.id,
            kind=quotas.MODEL_TURNS,
            extra=10,
            reason="Revoked straight away",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            granted_by=staff.id,
        )
        assert await quotas.revoke(allowance_id=row.id, revoked_by=staff.id)
        assert not await quotas.revoke(allowance_id=row.id, revoked_by=staff.id)
        assert (await quotas.usage(a.id, quotas.MODEL_TURNS)).limit == 3

    @pytest.mark.parametrize(
        "change",
        [
            {"reason": ""},
            {"reason": "ok"},
            {"extra": 0},
            {"extra": -5},
            {"expires_in": timedelta(hours=-1)},
            {"expires_in": timedelta(days=60)},
        ],
    )
    async def test_a_grant_needs_a_reason_a_size_and_an_end(
        self, people, quotas_on, change
    ):
        a, _, staff = people
        with pytest.raises(quotas.GrantRefused):
            await quotas.grant(
                user_id=a.id,
                kind=quotas.MODEL_TURNS,
                extra=change.get("extra", 5),
                reason=change.get("reason", "A real reason"),
                expires_at=datetime.now(UTC)
                + change.get("expires_in", timedelta(hours=1)),
                granted_by=staff.id,
            )

    async def test_a_grant_for_one_person_is_not_anothers(self, people, quotas_on):
        a, b, staff = people
        await quotas.grant(
            user_id=a.id,
            kind=quotas.MODEL_TURNS,
            extra=10,
            reason="Only for A",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            granted_by=staff.id,
        )
        assert (await quotas.usage(b.id, quotas.MODEL_TURNS)).limit == 3


class TestTheMessage:
    def test_it_says_the_limit_and_the_reset_in_local_time(self):
        usage = quotas.Usage(
            quotas.MODEL_TURNS, 50, 50, datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
        )
        line = quotas.message(usage, "Asia/Kolkata")
        assert "50 messages" in line
        assert "5:30 am" in line

    def test_a_bad_timezone_still_says_it(self):
        usage = quotas.Usage(
            quotas.VOICE_MINUTES, 1, 1, datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
        )
        assert "1 voice minute " in quotas.message(usage, "Not/AZone")


@pytest.mark.asyncio
class TestInTheThread:
    async def test_over_the_limit_decibyl_says_so_and_asks_no_model(
        self, people, quotas_on
    ):
        from api.services.workflow import decibyl

        a, _, _ = people
        await quotas.consume(a.id, quotas.MODEL_TURNS, 3)
        recorded = []

        async def record(**kwargs):
            recorded.append(kwargs)
            return 1

        with (
            patch.object(decibyl.agent_timeline, "record", side_effect=record),
            patch(
                "api.services.workflow.decibyl.db_client.get_all_workflows_for_listing",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl.visibility.role_of",
                AsyncMock(return_value="owner"),
            ),
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue,
        ):
            asked = await decibyl.ask(
                organization_id=1,
                user_id=a.id,
                text="One more question",
                attachments=[],
                line="One more question",
                preset=None,
                thread_id="t-1",
            )
        assert asked == []
        enqueue.assert_not_awaited()
        said = recorded[-1]
        assert said["payload"]["quota"]["kind"] == "model_turns"
        assert "3 messages" in said["payload"]["body"]
        assert said["thread_id"] == "t-1"

    async def test_within_the_limit_the_turn_is_queued_and_counted(
        self, people, quotas_on
    ):
        from api.services.workflow import decibyl

        a, _, _ = people
        with (
            patch.object(decibyl.agent_timeline, "record", AsyncMock(return_value=1)),
            patch(
                "api.services.workflow.decibyl.db_client.get_all_workflows_for_listing",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl.visibility.role_of",
                AsyncMock(return_value="owner"),
            ),
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue,
        ):
            await decibyl.ask(
                organization_id=1,
                user_id=a.id,
                text="Hello",
                attachments=[],
                line="Hello",
                preset=None,
            )
        enqueue.assert_awaited_once()
        assert (await quotas.usage(a.id, quotas.MODEL_TURNS)).used == 1


@asynccontextmanager
async def _client(user, staff=None):
    from api.app import app
    from api.services.auth.depends import get_staff, get_user

    app.dependency_overrides[get_user] = lambda: user
    if staff is not None:
        app.dependency_overrides[get_staff] = lambda: staff
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_user, None)
        app.dependency_overrides.pop(get_staff, None)


def _as_user(row, organization_id=None):
    return SimpleNamespace(
        id=row.id,
        provider_id=row.provider_id,
        selected_organization_id=organization_id,
        staff_role=None,
        email=None,
    )


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_routes_are_not_there(self, people):
        a, _, staff = people
        async with _client(_as_user(a), staff=_as_user(staff)) as client:
            assert (await client.get("/api/v1/me/quotas")).status_code == 404
            response = await client.get(f"/api/v1/admin/controls/quotas/users/{a.id}")
            assert response.status_code == 404

    async def test_a_person_reads_their_own_allowances(self, people, quotas_on):
        a, _, _ = people
        await quotas.consume(a.id, quotas.MODEL_TURNS, 2)
        async with _client(_as_user(a)) as client:
            response = await client.get("/api/v1/me/quotas")
        assert response.status_code == 200
        rows = {r["kind"]: r for r in response.json()["allowances"]}
        assert set(rows) == set(quotas.KINDS)
        assert rows["model_turns"]["remaining"] == 1

    async def test_staff_grant_through_the_console(self, people, quotas_on):
        a, _, staff = people
        await quotas.consume(a.id, quotas.MODEL_TURNS, 3)
        async with _client(_as_user(staff), staff=_as_user(staff)) as client:
            refused = await client.post(
                f"/api/v1/admin/controls/quotas/users/{a.id}/grants",
                json={"kind": "model_turns", "extra": 5, "reason": "", "hours": 2},
            )
            assert refused.status_code == 422
            granted = await client.post(
                f"/api/v1/admin/controls/quotas/users/{a.id}/grants",
                json={
                    "kind": "model_turns",
                    "extra": 5,
                    "reason": "Asked from Help for a demo",
                    "hours": 2,
                },
            )
            assert granted.status_code == 201
            seen = await client.get(f"/api/v1/admin/controls/quotas/users/{a.id}")
        rows = {r["kind"]: r for r in seen.json()["allowances"]}
        assert rows["model_turns"]["limit"] == 8
        assert seen.json()["grants"][0]["live"] is True

    async def test_a_person_cannot_grant_themselves_more(self, people, quotas_on):
        a, _, _ = people
        async with _client(_as_user(a)) as client:
            response = await client.post(
                f"/api/v1/admin/controls/quotas/users/{a.id}/grants",
                json={
                    "kind": "model_turns",
                    "extra": 5,
                    "reason": "Me, myself",
                    "hours": 2,
                },
            )
        # Not staff: the staff gate (which reads the real session) refuses.
        assert response.status_code in (401, 403)
