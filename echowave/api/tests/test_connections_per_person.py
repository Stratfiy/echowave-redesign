"""Connections per person (PRD v2 WS-1, KAN-196).

The promise: Decibyl uses the connections of the member talking to it. Two
members, one agent, two mailboxes -- and the API refuses cross-use before a
network call, not after Composio says no.

Off (the default) every helper answers "the workspace" and the tenant id
sent to Composio is byte-for-byte what it was.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import constants
from api.db import db_client
from api.enums import ToolCategory
from api.services.integrations.composio import client as composio_client
from api.services.integrations.composio import members
from api.services.workflow import connected_tools

ORG = 7
PRIYA = 3
RAMESH = 4


@pytest.fixture
def per_person(monkeypatch):
    monkeypatch.setattr(constants, "CONNECTIONS_PER_PERSON_ENABLED", True)


@pytest.fixture
def composio_key(monkeypatch):
    monkeypatch.setattr(composio_client, "COMPOSIO_API_KEY", "test-key")


def _gmail_tool(connected_account_id: str | None = None):
    config = {"toolkit": "GMAIL", "tool_slug": "GMAIL_SEND_EMAIL"}
    if connected_account_id:
        config["connected_account_id"] = connected_account_id
    return SimpleNamespace(
        name="Send email",
        tool_uuid="t-1",
        category=ToolCategory.COMPOSIO.value,
        definition={"config": config},
    )


def _patched_post(status_code=200, body=None):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = body if body is not None else {"data": {"ok": 1}}
    response.text = ""
    mock_client = AsyncMock()
    mock_client.post.return_value = response
    patcher = patch("api.services.integrations.composio.client.httpx.AsyncClient")
    mock_cls = patcher.start()
    mock_cls.return_value.__aenter__.return_value = mock_client
    return patcher, mock_client


def _toolkits(by_member: dict[int | None, list[str]]):
    """A connected_toolkits double keyed on the member asked about."""

    async def fake(organization_id, *, user_id=None, **_):
        return by_member.get(user_id, [])

    return fake


class TestTenantPerMember:
    """Test 1: the identity is namespaced per member, and per organization."""

    def test_off_every_member_is_the_workspace(self):
        assert composio_client.tenant_user_id(ORG, PRIYA) == "decibyl_org_7"
        assert composio_client.tenant_user_id(ORG) == "decibyl_org_7"

    def test_on_each_member_has_their_own(self, per_person):
        assert composio_client.tenant_user_id(ORG, PRIYA) == "decibyl_org_7_user_3"
        assert composio_client.tenant_user_id(ORG, PRIYA) != (
            composio_client.tenant_user_id(ORG, RAMESH)
        )
        assert composio_client.tenant_user_id(ORG, PRIYA) != (
            composio_client.tenant_user_id(8, PRIYA)
        )

    def test_on_the_workspace_tenant_is_unchanged(self, per_person):
        assert composio_client.tenant_user_id(ORG) == "decibyl_org_7"

    @pytest.mark.parametrize("bad", [None, 0, -1, True, "3", 3.0])
    def test_anything_that_is_not_a_member_is_the_workspace(self, per_person, bad):
        assert composio_client.tenant_user_id(ORG, bad) == "decibyl_org_7"

    def test_the_organization_is_still_checked_first(self, per_person):
        with pytest.raises(composio_client.ComposioNotConfigured):
            composio_client.tenant_user_id(None, PRIYA)


class TestActingAs:
    def test_the_scope_is_set_for_the_turn_and_cleared_after(self):
        assert members.acting_user() is None
        with members.acting_as(PRIYA):
            assert members.acting_user() == PRIYA
            with members.acting_as(RAMESH):
                assert members.acting_user() == RAMESH
            assert members.acting_user() == PRIYA
        assert members.acting_user() is None

    @pytest.mark.parametrize("bad", [None, 0, True, "3"])
    def test_a_non_member_sets_nothing(self, bad):
        with members.acting_as(bad):
            assert members.acting_user() is None


@pytest.mark.asyncio
class TestOwnershipRegistry:
    """Test 2: a connection row carries user_id; A's rows never list under B."""

    async def _org_and_two_members(self):
        # Fresh identities per run: the test database is not reset between
        # runs, and a registry row left behind would make this pass or fail
        # on history rather than on the code.
        run = uuid4().hex[:8]
        a, _ = await db_client.get_or_create_user_by_provider_id(f"ws1-priya-{run}")
        b, _ = await db_client.get_or_create_user_by_provider_id(f"ws1-ramesh-{run}")
        org, _ = await db_client.get_or_create_organization_by_provider_id(
            f"ws1-org-{run}", a.id
        )
        return org.id, a.id, b.id

    async def test_a_started_connect_is_a_row_and_the_id_is_learned_later(self):
        org, a, b = await self._org_and_two_members()
        row = await db_client.record_member_connection(
            organization_id=org, user_id=a, toolkit="GMAIL"
        )
        assert row.toolkit == "gmail" and row.connected_account_id is None
        # Idempotent: pressing Connect twice is one row.
        again = await db_client.record_member_connection(
            organization_id=org, user_id=a, toolkit="gmail"
        )
        assert again.id == row.id

        await db_client.learn_member_connections(
            organization_id=org, user_id=a, accounts=[("gmail", "ca_priya_gmail")]
        )
        mine = await db_client.member_connections(organization_id=org, user_id=a)
        assert [(r.toolkit, r.connected_account_id) for r in mine] == [
            ("gmail", "ca_priya_gmail")
        ]
        assert (
            await db_client.member_connection_owner(
                organization_id=org, connected_account_id="ca_priya_gmail"
            )
            == a
        )
        # B's listing never shows A's row, and B does not own it.
        assert await db_client.member_connections(organization_id=org, user_id=b) == []
        # An id nobody registered is nobody's: the workspace's, or unknown.
        assert (
            await db_client.member_connection_owner(
                organization_id=org, connected_account_id="ca_workspace"
            )
            is None
        )
        # Learning again is a no-op.
        await db_client.learn_member_connections(
            organization_id=org, user_id=a, accounts=[("gmail", "ca_priya_gmail")]
        )
        assert (
            len(await db_client.member_connections(organization_id=org, user_id=a)) == 1
        )


@pytest.mark.asyncio
class TestTheResolver:
    """Test 3: member's own → workspace's → a connect card, never an exception."""

    async def test_a_member_with_gmail_sends_from_their_own(self, per_person):
        with patch.object(
            composio_client,
            "connected_toolkits",
            _toolkits({PRIYA: ["GMAIL"], None: ["GMAIL"]}),
        ):
            resolved = await members.resolve(
                organization_id=ORG, toolkit="gmail", user_id=PRIYA
            )
        assert resolved == members.Resolved(members.MEMBER, PRIYA)

    async def test_a_member_without_falls_back_to_the_workspace(self, per_person):
        with patch.object(
            composio_client, "connected_toolkits", _toolkits({None: ["GMAIL"]})
        ):
            resolved = await members.resolve(
                organization_id=ORG, toolkit="gmail", user_id=RAMESH
            )
        assert resolved == members.Resolved(members.WORKSPACE, None)

    async def test_neither_is_a_card_and_a_blocked_answer_not_an_exception(
        self, per_person, composio_key
    ):
        patcher, http = _patched_post()
        offered = AsyncMock(return_value={"status": "offered"})
        try:
            with (
                patch.object(composio_client, "connected_toolkits", _toolkits({})),
                patch("api.services.workflow.connector_offer.offer", offered),
                members.acting_as(RAMESH),
            ):
                result = await connected_tools.execute(
                    organization_id=ORG,
                    tool=_gmail_tool(),
                    arguments={"to": "x@y"},
                    ref_id="r1",
                )
        finally:
            patcher.stop()
        assert result["status"] == "needs_connection"
        assert result["app"] == "gmail"
        offered.assert_awaited_once()
        assert offered.await_args.kwargs["arguments"]["app"] == "gmail"
        http.post.assert_not_called()

    async def test_the_call_carries_the_members_tenant(self, per_person, composio_key):
        patcher, http = _patched_post()
        try:
            with (
                patch.object(
                    composio_client, "connected_toolkits", _toolkits({PRIYA: ["GMAIL"]})
                ),
                patch("api.services.billing.events.charge_in_own_session", AsyncMock()),
                members.acting_as(PRIYA),
            ):
                result = await connected_tools.execute(
                    organization_id=ORG,
                    tool=_gmail_tool(),
                    arguments={},
                    ref_id="r2",
                )
        finally:
            patcher.stop()
        assert result["status"] == "success"
        assert http.post.call_args.kwargs["json"]["user_id"] == "decibyl_org_7_user_3"

    async def test_falling_back_carries_the_workspace_tenant(
        self, per_person, composio_key
    ):
        patcher, http = _patched_post()
        try:
            with (
                patch.object(
                    composio_client, "connected_toolkits", _toolkits({None: ["GMAIL"]})
                ),
                patch("api.services.billing.events.charge_in_own_session", AsyncMock()),
                members.acting_as(RAMESH),
            ):
                await connected_tools.execute(
                    organization_id=ORG, tool=_gmail_tool(), arguments={}, ref_id="r3"
                )
        finally:
            patcher.stop()
        assert http.post.call_args.kwargs["json"]["user_id"] == "decibyl_org_7"

    async def test_an_explicit_user_id_beats_the_turns_scope(
        self, per_person, composio_key
    ):
        """A card confirmed by Ramesh sends as Ramesh, whoever opened the turn."""
        patcher, http = _patched_post()
        try:
            with (
                patch.object(
                    composio_client,
                    "connected_toolkits",
                    _toolkits({RAMESH: ["GMAIL"]}),
                ),
                patch("api.services.billing.events.charge_in_own_session", AsyncMock()),
                members.acting_as(PRIYA),
            ):
                await connected_tools.execute(
                    organization_id=ORG,
                    tool=_gmail_tool(),
                    arguments={},
                    ref_id="r4",
                    user_id=RAMESH,
                )
        finally:
            patcher.stop()
        assert http.post.call_args.kwargs["json"]["user_id"] == "decibyl_org_7_user_4"


@pytest.mark.asyncio
class TestCrossUseIsRefused:
    """Test 4: A running a tool pinned to B's account is refused before I/O."""

    async def test_another_members_account_never_reaches_the_network(
        self, per_person, composio_key
    ):
        patcher, http = _patched_post()
        try:
            with patch.object(members, "owner_of", AsyncMock(return_value=RAMESH)):
                result = await composio_client.execute_tool(
                    tool_slug="GMAIL_SEND_EMAIL",
                    arguments={},
                    organization_id=ORG,
                    connected_account_id="ca_ramesh",
                    user_id=PRIYA,
                )
        finally:
            patcher.stop()
        assert result["status"] == "error"
        assert "another member" in result["error"]
        http.post.assert_not_called()

    async def test_your_own_account_goes_through_under_your_tenant(
        self, per_person, composio_key
    ):
        patcher, http = _patched_post()
        try:
            with patch.object(members, "owner_of", AsyncMock(return_value=PRIYA)):
                result = await composio_client.execute_tool(
                    tool_slug="GMAIL_SEND_EMAIL",
                    arguments={},
                    organization_id=ORG,
                    connected_account_id="ca_priya",
                    user_id=PRIYA,
                )
        finally:
            patcher.stop()
        assert result["status"] == "success"
        sent = http.post.call_args.kwargs["json"]
        assert sent["user_id"] == "decibyl_org_7_user_3"
        assert sent["connected_account_id"] == "ca_priya"

    async def test_the_workspaces_account_is_refused_for_a_member_by_the_vendor(
        self, per_person, composio_key
    ):
        """An id the registry does not know goes out under the member's
        tenant; Composio refuses an account that is not under it. A slower
        refusal, never a crossing."""
        patcher, http = _patched_post()
        try:
            with patch.object(members, "owner_of", AsyncMock(return_value=None)):
                await composio_client.execute_tool(
                    tool_slug="GMAIL_SEND_EMAIL",
                    arguments={},
                    organization_id=ORG,
                    connected_account_id="ca_unknown",
                    user_id=PRIYA,
                )
        finally:
            patcher.stop()
        assert http.post.call_args.kwargs["json"]["user_id"] == "decibyl_org_7_user_3"


@pytest.mark.asyncio
class TestFlagOff:
    """Test 5: every path behaves as today."""

    async def test_the_turns_member_changes_nothing_about_the_call(self, composio_key):
        patcher, http = _patched_post()

        async def never(*_, **__):
            raise AssertionError("the resolver must not run with the flag off")

        try:
            with (
                patch.object(composio_client, "connected_toolkits", never),
                patch.object(members, "owner_of", never),
                patch("api.services.billing.events.charge_in_own_session", AsyncMock()),
                members.acting_as(PRIYA),
            ):
                result = await connected_tools.execute(
                    organization_id=ORG,
                    tool=_gmail_tool("ca_anyone"),
                    arguments={},
                    ref_id="r5",
                    user_id=PRIYA,
                )
        finally:
            patcher.stop()
        assert result["status"] == "success"
        sent = http.post.call_args.kwargs["json"]
        assert sent["user_id"] == "decibyl_org_7"
        assert sent["connected_account_id"] == "ca_anyone"


class TestTheFlag:
    def test_it_is_registered_and_off_by_default(self):
        from api.services import features

        assert features.FLAGS["connections_per_person"] == (
            "CONNECTIONS_PER_PERSON_ENABLED"
        )
        assert constants.CONNECTIONS_PER_PERSON_ENABLED is False


class TestTheRoutes:
    """A member connects for themselves; the screen tells Mine from Workspace."""

    def _app(self, user_id=PRIYA):
        from api.routes.connectors import router
        from api.services.auth.depends import get_user

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=user_id, selected_organization_id=ORG
        )
        return TestClient(app)

    def test_connect_for_me_is_a_member_route_gated_by_the_flag(self):
        from api.tests.test_connector_permissions import _minimum_role

        assert _minimum_role("/api/v1/connectors/{slug}/connect/mine", "POST") is None
        assert (
            self._app().post("/api/v1/connectors/gmail/connect/mine").status_code == 404
        )

    def test_connect_for_me_mints_a_link_under_the_members_tenant(self, per_person):
        link = AsyncMock(return_value={"url": "https://c/x", "expires_at": None})
        record = AsyncMock()
        with (
            patch("api.routes.connectors.is_configured", return_value=True),
            patch(
                "api.routes.connectors.connected_toolkits",
                _toolkits({PRIYA: [], None: ["GMAIL"]}),
            ),
            patch(
                "api.routes.connectors.toolkit_name", AsyncMock(return_value="Gmail")
            ),
            patch("api.routes.connectors.connect_link", link),
            patch("api.routes.connectors.db_client.record_member_connection", record),
        ):
            response = self._app().post("/api/v1/connectors/gmail/connect/mine")
        assert response.status_code == 200, response.text
        assert response.json()["connect_url"] == "https://c/x"
        assert link.await_args.kwargs == {
            "toolkit": "gmail",
            "organization_id": ORG,
            "user_id": PRIYA,
        }
        assert record.await_args.kwargs == {
            "organization_id": ORG,
            "user_id": PRIYA,
            "toolkit": "gmail",
        }

    def test_an_app_already_yours_gets_no_second_link(self, per_person):
        with (
            patch("api.routes.connectors.is_configured", return_value=True),
            patch(
                "api.routes.connectors.connected_toolkits",
                _toolkits({PRIYA: ["GMAIL"]}),
            ),
            patch("api.routes.connectors.connect_link") as link,
        ):
            response = self._app().post("/api/v1/connectors/gmail/connect/mine")
        assert response.status_code == 409
        link.assert_not_called()

    def test_accounts_are_split_into_mine_and_workspace(self, per_person):
        async def accounts(organization_id, *, user_id=None, **_):
            if user_id == PRIYA:
                return [
                    {
                        "connected_account_id": "ca_priya",
                        "app": "gmail",
                        "label": "priya",
                        "connected_at": None,
                    }
                ]
            return [
                {
                    "connected_account_id": "ca_ws",
                    "app": "gmail",
                    "label": "office",
                    "connected_at": None,
                }
            ]

        learn = AsyncMock()
        with (
            patch("api.routes.connectors.is_configured", return_value=True),
            patch("api.routes.connectors.connected_accounts", accounts),
            patch("api.routes.connectors.members.learn", learn),
        ):
            response = self._app().get("/api/v1/connectors/accounts")
        rows = response.json()["accounts"]
        assert [(r["connected_account_id"], r["scope"]) for r in rows] == [
            ("ca_priya", "mine"),
            ("ca_ws", "workspace"),
        ]
        # The registry learns whose ids these are from this very listing.
        assert learn.await_args.kwargs["user_id"] == PRIYA
        assert learn.await_args.kwargs["accounts"][0]["connected_account_id"] == (
            "ca_priya"
        )

    def test_off_every_account_is_the_workspaces(self):
        async def accounts(organization_id, *, user_id=None, **_):
            assert user_id is None
            return [
                {
                    "connected_account_id": "ca_ws",
                    "app": "gmail",
                    "label": "office",
                    "connected_at": None,
                }
            ]

        with (
            patch("api.routes.connectors.is_configured", return_value=True),
            patch("api.routes.connectors.connected_accounts", accounts),
        ):
            rows = self._app().get("/api/v1/connectors/accounts").json()["accounts"]
        assert [r["scope"] for r in rows] == ["workspace"]

    def test_the_catalogue_says_whose_connection_it_is(self, per_person):
        rows = [
            SimpleNamespace(
                slug=slug,
                name=slug.title(),
                description="",
                logo=None,
                setup="one_click",
                tools_count=1,
                group="Email",
                setup_url=None,
                also_connectable=False,
            )
            for slug in ("gmail", "outlook", "slack", "zoho")
        ]
        with (
            patch("api.routes.connectors.is_configured", return_value=True),
            patch(
                "api.routes.connectors.catalogue.connectors",
                AsyncMock(return_value=rows),
            ),
            patch("api.routes.connectors.catalogue.search", lambda rows, q: rows),
            patch("api.routes.connectors.catalogue.GROUPS", [("Email", None)]),
            patch(
                "api.routes.connectors.connected_toolkits",
                _toolkits({PRIYA: ["GMAIL", "SLACK"], None: ["GMAIL", "OUTLOOK"]}),
            ),
        ):
            body = self._app().get("/api/v1/connectors").json()
        by_slug = {c["slug"]: c for g in body["groups"] for c in g["connectors"]}
        assert by_slug["gmail"]["connected_by"] == "both"
        assert by_slug["slack"]["connected_by"] == "me"
        assert by_slug["outlook"]["connected_by"] == "workspace"
        assert by_slug["zoho"]["connected_by"] is None
        assert by_slug["slack"]["connected"] is True
        assert by_slug["zoho"]["connected"] is False
