"""The read side of the timeline, and the three things it must not do.

The table has been written to since it shipped and read by nothing. That is
why this file leans on what must *appear* rather than on what the handler
returns for a happy path: the defect being fixed is an absence, and a test
that only checks a shape would have passed throughout the whole outage.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.agent_timeline import timeline
from api.routes.main import router as api_router


def _user(org=42):
    return SimpleNamespace(selected_organization_id=org)


def _row(id=1, **kwargs):
    fields = dict(
        id=id,
        at=datetime.now(UTC),
        kind="outcome_filed",
        actor="agent",
        summary="Booked Tuesday 4pm for Ramesh",
        payload={},
        is_deliverable=True,
        workflow_id=7,
        workflow_run_id=None,
        folder_id=None,
    )
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def _events(rows):
    return patch(
        "api.routes.agent_timeline.db_client.agent_events",
        AsyncMock(return_value=rows),
    )


def _owns_workflow(found=True):
    return patch(
        "api.routes.agent_timeline.db_client.get_workflow",
        AsyncMock(return_value=SimpleNamespace(id=7) if found else None),
    )


def _owns_run(found=True):
    return patch(
        "api.routes.agent_timeline.db_client.get_workflow_run",
        AsyncMock(return_value=SimpleNamespace(id=336) if found else None),
    )


class TestItIsReachableAtAll:
    def test_the_router_is_mounted(self):
        """The whole point of this change. A route file nothing includes is
        the same write-only silence in a new place."""
        paths = {route.path for route in api_router.routes}
        assert "/timeline" in paths


class TestOwnership:
    @pytest.mark.asyncio
    async def test_another_accounts_bot_is_not_found_rather_than_empty(self):
        """An empty list reads as "that bot has been quiet", which would let
        somebody walk the id space and learn which bots exist."""
        with _owns_workflow(found=False), _events([]) as read:
            with pytest.raises(HTTPException) as raised:
                await timeline(workflow_id=7, user=_user())
        assert raised.value.status_code == 404
        read.assert_not_called()

    @pytest.mark.asyncio
    async def test_another_accounts_run_is_not_found(self):
        with _owns_run(found=False), _events([]) as read:
            with pytest.raises(HTTPException) as raised:
                await timeline(workflow_run_id=336, user=_user())
        assert raised.value.status_code == 404
        read.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_organisation_comes_from_the_session_not_the_request(self):
        with _events([]) as read:
            await timeline(user=_user(org=42))
        assert read.await_args.kwargs["organization_id"] == 42

    @pytest.mark.asyncio
    async def test_a_session_with_no_organisation_is_refused(self):
        with pytest.raises(HTTPException) as raised:
            await timeline(user=_user(org=None))
        assert raised.value.status_code == 400


class TestConsent:
    @pytest.mark.asyncio
    async def test_transcripts_are_off_unless_this_call_asked_for_them(self):
        """A caller was told what the recording was for. A default that shows
        it because nobody thought about the default has broken that."""
        with _events([]) as read:
            await timeline(user=_user())
        assert read.await_args.kwargs["include_on_request"] is False

    @pytest.mark.asyncio
    async def test_asking_for_transcripts_passes_it_through(self):
        with _events([]) as read:
            await timeline(include_transcripts=True, user=_user())
        assert read.await_args.kwargs["include_on_request"] is True


class TestPaging:
    @pytest.mark.asyncio
    async def test_a_full_page_hands_back_a_cursor(self):
        rows = [_row(id=i) for i in range(9, 6, -1)]
        with _events(rows):
            response = await timeline(limit=3, user=_user())
        assert response.next_before_id == 7

    @pytest.mark.asyncio
    async def test_a_short_page_is_the_end(self):
        with _events([_row(id=9)]):
            response = await timeline(limit=3, user=_user())
        assert response.next_before_id is None

    @pytest.mark.asyncio
    async def test_one_call_is_never_paged_backwards(self):
        """A call reads forwards, so its last id is its newest event. Handing
        that back as a cursor would page a reader away from the story."""
        rows = [_row(id=i, workflow_run_id=336) for i in (7, 8, 9)]
        with _owns_run(), _events(rows):
            response = await timeline(workflow_run_id=336, limit=3, user=_user())
        assert response.next_before_id is None


class TestShape:
    @pytest.mark.asyncio
    async def test_an_unknown_kind_still_renders(self):
        """Kinds are strings in the column precisely so a newer writer does
        not need a migration. Validating against an enum here would blank the
        whole feed the first time one appeared."""
        with _events([_row(kind="something_invented_next_year")]):
            response = await timeline(user=_user())
        assert response.events[0].kind == "something_invented_next_year"

    @pytest.mark.asyncio
    async def test_a_null_payload_is_an_object_not_none(self):
        with _events([_row(payload=None)]):
            response = await timeline(user=_user())
        assert response.events[0].payload == {}
