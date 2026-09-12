"""The ignition for routines: creating one, arming one, testing one.

The machinery shipped without these — the table, the tick and the runner all
existed with no route touching them, so a routine could only be created by
writing SQL and the one pack that needs one could not be given one.

The tests that matter are the refusals. A toggle that appears to move and does
nothing is worse than one that will not move, because the operator walks away
believing the bot is watching their deadlines.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.db import db_client
from api.enums import OrganizationRole
from api.routes import routines as route
from api.schemas.routine import RoutineWrite


def _user(organization_id=7):
    return SimpleNamespace(
        id=1, provider_id="u1", selected_organization_id=organization_id
    )


def _routine(**kwargs):
    base = dict(
        id=5,
        organization_id=7,
        workflow_id=42,
        name="Morning numbers",
        instruction="Summarise yesterday.",
        cadence="daily",
        anchor="opening",
        at_minute=0,
        offset_minutes=0,
        weekday=0,
        needs_apps=[],
        is_active=False,
        tested_at=None,
        last_fired_at=None,
        last_skipped_reason=None,
        last_skipped_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _prefs():
    return SimpleNamespace(
        timezone="Asia/Kolkata",
        business_hours={
            "enabled": True,
            "slots": [
                {"day_of_week": d, "start_time": "09:30", "end_time": "18:00"}
                for d in range(5)
            ],
        },
    )


def _patches(workflow=SimpleNamespace(id=42, folder_id=None)):
    import contextlib

    stack = contextlib.ExitStack()
    stack.enter_context(
        patch.object(db_client, "get_workflow", AsyncMock(return_value=workflow))
    )
    stack.enter_context(
        patch.object(
            route, "get_organization_preferences", AsyncMock(return_value=_prefs())
        )
    )
    return stack


class TestArmingIsGated:
    """A routine arms only after a test run, refused twice: here and in the
    tick."""

    @pytest.mark.asyncio
    async def test_switching_on_an_untested_routine_is_refused_with_the_reason(self):
        with (
            _patches(),
            patch.object(
                db_client,
                "get_routine",
                AsyncMock(return_value=_routine(tested_at=None)),
            ),
        ):
            with pytest.raises(HTTPException) as raised:
                await route.set_active(
                    workflow_id=42, routine_id=5, active=True, user=_user()
                )
        assert raised.value.status_code == 400
        assert "Test run it first" in raised.value.detail
        # And it says why, because "test it first" without a reason reads as
        # bureaucracy.
        assert "real data" in raised.value.detail

    @pytest.mark.asyncio
    async def test_a_tested_routine_may_be_switched_on(self):
        tested = _routine(tested_at=datetime(2026, 1, 1, tzinfo=UTC))
        with (
            _patches(),
            patch.object(db_client, "get_routine", AsyncMock(return_value=tested)),
            patch.object(
                db_client,
                "set_routine_active",
                AsyncMock(
                    return_value=_routine(tested_at=tested.tested_at, is_active=True)
                ),
            ),
        ):
            result = await route.set_active(
                workflow_id=42, routine_id=5, active=True, user=_user()
            )
        assert result.is_active is True

    @pytest.mark.asyncio
    async def test_switching_off_never_refuses(self):
        """Whatever state it is in, stopping it must always be possible."""
        with (
            _patches(),
            patch.object(
                db_client,
                "get_routine",
                AsyncMock(return_value=_routine(tested_at=None)),
            ),
            patch.object(
                db_client, "set_routine_active", AsyncMock(return_value=_routine())
            ),
        ):
            result = await route.set_active(
                workflow_id=42, routine_id=5, active=False, user=_user()
            )
        assert result.is_active is False


class TestTesting:
    @pytest.mark.asyncio
    async def test_a_test_run_stamps_tested_and_enqueues_the_real_run(self):
        enqueued = []
        with (
            _patches(),
            patch.object(db_client, "get_routine", AsyncMock(return_value=_routine())),
            patch.object(
                db_client, "mark_routine_tested", AsyncMock(return_value=True)
            ),
            patch.object(
                route,
                "enqueue_job",
                AsyncMock(side_effect=lambda *a: enqueued.append(a)),
            ),
        ):
            result = await route.test_routine(
                workflow_id=42, routine_id=5, user=_user()
            )
        assert result.started is True
        assert enqueued == [("run_agent_routine", 5)]

    @pytest.mark.asyncio
    async def test_a_queue_failure_is_a_503_and_not_a_silent_success(self):
        # A test that reports success while nothing runs would arm a routine
        # on the strength of a run that never happened.
        with (
            _patches(),
            patch.object(db_client, "get_routine", AsyncMock(return_value=_routine())),
            patch.object(
                db_client, "mark_routine_tested", AsyncMock(return_value=True)
            ),
            patch.object(
                route, "enqueue_job", AsyncMock(side_effect=RuntimeError("redis gone"))
            ),
        ):
            with pytest.raises(HTTPException) as raised:
                await route.test_routine(workflow_id=42, routine_id=5, user=_user())
        assert raised.value.status_code == 503

    @pytest.mark.asyncio
    async def test_the_reply_tells_them_where_to_look(self):
        with (
            _patches(),
            patch.object(db_client, "get_routine", AsyncMock(return_value=_routine())),
            patch.object(
                db_client, "mark_routine_tested", AsyncMock(return_value=True)
            ),
            patch.object(route, "enqueue_job", AsyncMock()),
        ):
            result = await route.test_routine(
                workflow_id=42, routine_id=5, user=_user()
            )
        assert "thread" in result.detail


class TestScoping:
    @pytest.mark.asyncio
    async def test_another_accounts_agent_is_not_found(self):
        with patch.object(db_client, "get_workflow", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as raised:
                await route.list_routines(workflow_id=999, user=_user())
        assert raised.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_routine_belonging_to_a_different_bot_is_not_found(self):
        """Both ids are checked. Checking only the routine would let it be
        reached through another bot's path -- same account, wrong bot."""
        with (
            _patches(),
            patch.object(
                db_client,
                "get_routine",
                AsyncMock(return_value=_routine(workflow_id=99)),
            ),
        ):
            with pytest.raises(HTTPException) as raised:
                await route.set_active(
                    workflow_id=42, routine_id=5, active=False, user=_user()
                )
        assert raised.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_selected_organization_is_a_400(self):
        with pytest.raises(HTTPException) as raised:
            await route.list_routines(workflow_id=42, user=_user(organization_id=None))
        assert raised.value.status_code == 400


class TestCreating:
    @pytest.mark.asyncio
    async def test_a_new_routine_is_off_and_untested(self):
        with (
            _patches(),
            patch.object(
                db_client, "routines_for_workflow", AsyncMock(return_value=[])
            ),
            patch.object(
                db_client, "create_routine", AsyncMock(return_value=_routine())
            ),
        ):
            result = await route.create_routine(
                workflow_id=42,
                body=RoutineWrite(name="Morning numbers"),
                user=_user(),
            )
        assert result.is_active is False
        assert result.may_arm is False

    @pytest.mark.asyncio
    async def test_the_per_bot_ceiling_is_enforced_with_a_readable_reason(self):
        # The tick reads every armed routine every minute, and a bot with
        # thirty is not a bot anybody can reason about.
        with (
            _patches(),
            patch.object(
                db_client,
                "routines_for_workflow",
                AsyncMock(return_value=[_routine()] * route.MAX_PER_WORKFLOW),
            ),
        ):
            with pytest.raises(HTTPException) as raised:
                await route.create_routine(
                    workflow_id=42, body=RoutineWrite(name="One more"), user=_user()
                )
        assert raised.value.status_code == 400
        assert str(route.MAX_PER_WORKFLOW) in raised.value.detail


class TestWhatAScreenIsGiven:
    @pytest.mark.asyncio
    async def test_the_schedule_reads_as_a_sentence_and_names_the_anchor(self):
        # "Every weekday when you open" tells an operator that moving their
        # hours moves the run. "Every weekday at 09:30" hides it.
        with (
            _patches(),
            patch.object(
                db_client,
                "routines_for_workflow",
                AsyncMock(return_value=[_routine(cadence="weekdays")]),
            ),
        ):
            listed = await route.list_routines(workflow_id=42, user=_user())
        assert listed.routines[0].schedule_summary == "Every weekday when you open"

    @pytest.mark.asyncio
    async def test_the_next_run_is_computed_in_the_organisations_zone(self):
        with (
            _patches(),
            patch.object(
                db_client, "routines_for_workflow", AsyncMock(return_value=[_routine()])
            ),
        ):
            listed = await route.list_routines(workflow_id=42, user=_user())
        nxt = listed.routines[0].next_run_at
        assert nxt is not None
        # 09:30 in the business's own zone, not in UTC.
        assert (nxt.hour, nxt.minute) == (9, 30)

    @pytest.mark.asyncio
    async def test_may_arm_is_derived_not_stored(self):
        with (
            _patches(),
            patch.object(
                db_client,
                "routines_for_workflow",
                AsyncMock(
                    return_value=[
                        _routine(id=1, tested_at=None),
                        _routine(id=2, tested_at=datetime(2026, 1, 1, tzinfo=UTC)),
                    ]
                ),
            ),
        ):
            listed = await route.list_routines(workflow_id=42, user=_user())
        assert [r.may_arm for r in listed.routines] == [False, True]


class TestPermissions:
    """Read the policy off the mounted app rather than off the function.

    Same mechanism test_connector_permissions.py uses, and the reason it
    exists is in that file: "without it a permission can only be enforced,
    never enumerated -- and an ungated route looks exactly like a gated one
    from outside."
    """

    @staticmethod
    def _minimum(method: str, path: str):
        from api.app import app

        for mounted in app.routes:
            if getattr(mounted, "path", None) != path:
                continue
            if method.upper() not in (getattr(mounted, "methods", None) or set()):
                continue
            found: list[str] = []
            stack = [getattr(mounted, "dependant", None)]
            while stack:
                node = stack.pop()
                if node is None:
                    continue
                role = getattr(
                    getattr(node, "call", None), "__org_role_minimum__", None
                )
                if role:
                    found.append(role)
                stack.extend(getattr(node, "dependencies", None) or [])
            return found
        raise AssertionError(f"No route {method} {path}")

    def test_every_write_needs_admin(self):
        """Same gate connectors got: a routine acts unsupervised against
        connected apps, so creating one is closer to granting access than to
        editing a prompt."""
        for method, path in (
            ("POST", "/api/v1/workflows/{workflow_id}/routines"),
            ("PUT", "/api/v1/workflows/{workflow_id}/routines/{routine_id}"),
            ("DELETE", "/api/v1/workflows/{workflow_id}/routines/{routine_id}"),
            ("POST", "/api/v1/workflows/{workflow_id}/routines/{routine_id}/active"),
            ("POST", "/api/v1/workflows/{workflow_id}/routines/{routine_id}/test"),
        ):
            assert OrganizationRole.ADMIN.value in self._minimum(method, path), (
                method,
                path,
            )

    def test_reading_them_does_not(self):
        """A member can see what the bots are scheduled to do. Only an admin
        can change it."""
        assert self._minimum("GET", "/api/v1/workflows/{workflow_id}/routines") == []
