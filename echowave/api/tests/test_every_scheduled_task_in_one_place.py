"""Every scheduled task the account has, on one screen.

Routines are per bot: created, listed and armed under
``/workflows/{id}/routines``. That is right for the bot's own screen and
useless for the question an operator actually asks, which is "what runs
tomorrow morning". Answering it meant knowing which bots to look inside, so
nobody asked it, and the schedule the business runs on was invisible.

So one org-scoped listing, carrying each routine's bot name: a schedule
without the name of the thing it runs is a time and nothing else.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes import routines as route


def _user(organization_id=7):
    return SimpleNamespace(
        id=1, provider_id="u1", selected_organization_id=organization_id
    )


def _routine(**kwargs):
    base = dict(
        id=5,
        organization_id=7,
        workflow_id=42,
        name="Morning brief",
        instruction="Read the inbox.",
        cadence="daily",
        anchor="clock",
        at_minute=480,
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


class TestTheAccountsSchedule:
    async def test_it_lists_every_bots_routines(self):
        rows = [
            _routine(id=1, workflow_id=42, name="Morning brief"),
            _routine(id=2, workflow_id=43, name="Overdue watcher"),
        ]
        with (
            patch.object(
                route.db_client,
                "routines_for_organization",
                AsyncMock(return_value=rows),
            ),
            patch.object(
                route.db_client,
                "get_workflow_by_id",
                AsyncMock(
                    side_effect=lambda wid: SimpleNamespace(id=wid, name=f"Bot {wid}")
                ),
            ),
        ):
            out = await route.list_all_routines(user=_user())
        assert [r.name for r in out.routines] == ["Morning brief", "Overdue watcher"]

    async def test_each_row_carries_the_bot_it_belongs_to(self):
        """A time with no bot name beside it is not an answer to "what runs
        tomorrow"."""
        with (
            patch.object(
                route.db_client,
                "routines_for_organization",
                AsyncMock(return_value=[_routine(workflow_id=42)]),
            ),
            patch.object(
                route.db_client,
                "get_workflow_by_id",
                AsyncMock(return_value=SimpleNamespace(id=42, name="Inbox Brief")),
            ),
        ):
            out = await route.list_all_routines(user=_user())
        assert out.routines[0].workflow_name == "Inbox Brief"

    async def test_a_deleted_bot_does_not_break_the_screen(self):
        """The routine outlives its bot for as long as it takes somebody to
        notice. A listing that raised here would hide every other row."""
        with (
            patch.object(
                route.db_client,
                "routines_for_organization",
                AsyncMock(return_value=[_routine(workflow_id=999)]),
            ),
            patch.object(
                route.db_client, "get_workflow_by_id", AsyncMock(return_value=None)
            ),
        ):
            out = await route.list_all_routines(user=_user())
        assert out.routines[0].workflow_name is None

    async def test_no_organisation_is_refused(self):
        with pytest.raises(HTTPException) as exc:
            await route.list_all_routines(user=_user(organization_id=None))
        assert exc.value.status_code == 400

    async def test_an_account_with_none_gets_an_empty_list(self):
        with patch.object(
            route.db_client, "routines_for_organization", AsyncMock(return_value=[])
        ):
            out = await route.list_all_routines(user=_user())
        assert out.routines == []
