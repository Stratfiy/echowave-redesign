"""Who can see which agent (KAN-158)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.db import db_client as client
from api.services.workflow import visibility


@pytest.fixture
def roles_on(monkeypatch):
    monkeypatch.setattr(constants, "WORKSPACE_ROLES_ENABLED", True)


def _agent(vis=None, **extra):
    return SimpleNamespace(id=1, name="Invoices", visibility=vis or "everyone", **extra)


class TestTheRule:
    def test_everyone_is_the_default_and_every_role_sees_it(self, roles_on):
        for role in ("member", "admin", "owner", None):
            assert visibility.visible(_agent(), role) is True

    def test_admins_only_hides_from_members_and_the_unknown(self, roles_on):
        agent = _agent("admins")
        assert visibility.visible(agent, "member") is False
        assert visibility.visible(agent, None) is False
        assert visibility.visible(agent, "admin") is True
        assert visibility.visible(agent, "owner") is True

    def test_a_row_without_the_column_is_everyone(self, roles_on):
        assert visibility.visible(SimpleNamespace(id=2, name="x"), "member") is True

    def test_flag_off_hides_nothing(self, monkeypatch):
        monkeypatch.setattr(constants, "WORKSPACE_ROLES_ENABLED", False)
        assert visibility.visible(_agent("admins"), "member") is True

    def test_only_visible_keeps_order(self, roles_on):
        rows = [_agent(), _agent("admins"), _agent()]
        assert visibility.only_visible(rows, "member") == [rows[0], rows[2]]
        assert visibility.only_visible(rows, "admin") == rows

    def test_clean_accepts_the_two_words_only(self):
        assert visibility.clean(" Admins ") == "admins"
        assert visibility.clean(None) == "everyone"
        with pytest.raises(ValueError):
            visibility.clean("secret")


class TestTheViewer:
    @pytest.mark.asyncio
    async def test_the_role_comes_from_the_membership_row(self, roles_on):
        with patch.object(
            client,
            "get_membership",
            new=AsyncMock(return_value=SimpleNamespace(role="admin")),
        ) as get:
            assert await visibility.role_of(32, 7) == "admin"
        get.assert_awaited_once_with(32, 7)

    @pytest.mark.asyncio
    async def test_no_row_or_no_viewer_is_none(self, roles_on):
        with patch.object(client, "get_membership", new=AsyncMock(return_value=None)):
            assert await visibility.role_of(32, 7) is None
        assert await visibility.role_of(None, 7) is None

    @pytest.mark.asyncio
    async def test_flag_off_looks_nothing_up(self, monkeypatch):
        monkeypatch.setattr(constants, "WORKSPACE_ROLES_ENABLED", False)
        with patch.object(client, "get_membership", new=AsyncMock()) as get:
            assert await visibility.role_of(32, 7) is None
        get.assert_not_awaited()


# --- the routes, for a person -------------------------------------------------

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from api.tests.test_workflow_list_route import _make_test_app


def _row(id: int, vis: str):
    return SimpleNamespace(
        id=id,
        name=f"Agent {id}",
        status="active",
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        folder_id=None,
        workflow_uuid=f"u-{id}",
        is_live=True,
        handle=f"agent-{id}",
        visibility=vis,
        avatar=None,
    )


class TestTheListForAPerson:
    def test_a_member_does_not_get_the_admins_only_agent(self, roles_on):
        app = _make_test_app()
        client_ = TestClient(app)
        rows = [_row(1, "everyone"), _row(2, "admins")]
        with (
            patch("api.routes.workflow.db_client") as mock_db,
            patch(
                "api.routes.workflow.visibility.role_of",
                new=AsyncMock(return_value="member"),
            ),
        ):
            mock_db.get_all_workflows_for_listing = AsyncMock(return_value=rows)
            mock_db.get_workflow_run_counts = AsyncMock(return_value={})
            mock_db.get_squad_workflow_ids = AsyncMock(return_value=set())
            response = client_.get("/workflow/fetch")
        assert response.status_code == 200
        assert [w["id"] for w in response.json()] == [1]
        assert response.json()[0]["visibility"] == "everyone"

    def test_an_admin_gets_both(self, roles_on):
        app = _make_test_app()
        client_ = TestClient(app)
        rows = [_row(1, "everyone"), _row(2, "admins")]
        with (
            patch("api.routes.workflow.db_client") as mock_db,
            patch(
                "api.routes.workflow.visibility.role_of",
                new=AsyncMock(return_value="admin"),
            ),
        ):
            mock_db.get_all_workflows_for_listing = AsyncMock(return_value=rows)
            mock_db.get_workflow_run_counts = AsyncMock(return_value={})
            mock_db.get_squad_workflow_ids = AsyncMock(return_value=set())
            response = client_.get("/workflow/fetch")
        assert [w["id"] for w in response.json()] == [1, 2]


class TestSettingIt:
    def test_an_admin_sets_it_and_a_bad_word_is_refused(self, roles_on):
        app = _make_test_app()
        client_ = TestClient(app)
        with (
            patch("api.routes.workflow.db_client") as mock_db,
            patch(
                "api.services.auth.depends.db_client.get_membership",
                new=AsyncMock(return_value=SimpleNamespace(role="admin")),
            ),
        ):
            mock_db.set_workflow_visibility = AsyncMock(
                return_value=SimpleNamespace(id=2, visibility="admins")
            )
            ok = client_.put("/workflow/2/visibility", json={"visibility": "Admins"})
            bad = client_.put("/workflow/2/visibility", json={"visibility": "secret"})
        assert ok.status_code == 200 and ok.json() == {"id": 2, "visibility": "admins"}
        assert (
            mock_db.set_workflow_visibility.await_args.kwargs["visibility"] == "admins"
        )
        assert bad.status_code == 400

    def test_a_member_may_not_set_it(self, roles_on):
        app = _make_test_app()
        client_ = TestClient(app)
        with (
            patch("api.routes.workflow.db_client"),
            patch(
                "api.services.auth.depends.db_client.get_membership",
                new=AsyncMock(return_value=SimpleNamespace(role="member")),
            ),
        ):
            response = client_.put(
                "/workflow/2/visibility", json={"visibility": "admins"}
            )
        assert response.status_code == 403

    def test_flag_off_the_route_is_absent(self, monkeypatch):
        monkeypatch.setattr(constants, "WORKSPACE_ROLES_ENABLED", False)
        app = _make_test_app()
        client_ = TestClient(app)
        with patch("api.routes.workflow.db_client"):
            response = client_.put(
                "/workflow/2/visibility", json={"visibility": "admins"}
            )
        assert response.status_code == 404


class TestTheBoardsNames:
    @pytest.mark.asyncio
    async def test_a_members_board_omits_the_hidden_agent(self, roles_on):
        from api.services.workflow import tasks_board

        rows = [
            _agent(),
            SimpleNamespace(id=9, name="Invoices", visibility="admins", handle="inv"),
        ]
        with (
            patch.object(
                client,
                "get_all_workflows_for_listing",
                new=AsyncMock(return_value=rows),
            ),
            patch.object(
                client, "list_organization_members", new=AsyncMock(return_value=[])
            ),
            patch.object(
                client, "get_organization_by_id", new=AsyncMock(return_value=None)
            ),
        ):
            for_member = await tasks_board.board_context(
                7, viewer_role="member", for_person=True
            )
            for_admin = await tasks_board.board_context(
                7, viewer_role="admin", for_person=True
            )
            for_a_run = await tasks_board.board_context(7)
        assert list(for_member["names"]) == [1]
        assert list(for_admin["names"]) == [1, 9]
        assert list(for_a_run["names"]) == [1, 9]
