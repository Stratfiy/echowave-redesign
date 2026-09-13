"""The workspace name reaches the switcher, and an admin can set it.

``GET /organizations/mine`` selected ``organizations.name`` from #200 on, and
the table had no such column: the query raised before it reached Postgres and
every account was "Organization {id}". The first test here is the one that
would have caught it -- it runs the real query against the real schema.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.db.models import OrganizationMembershipModel, OrganizationModel, UserModel
from api.enums import OrganizationRole


async def _account(async_session, *, name, role):
    org = OrganizationModel(provider_id=f"org-{role}-{name}", name=name)
    user = UserModel(provider_id=f"user-{role}-{name}")
    async_session.add_all([org, user])
    await async_session.flush()
    user.selected_organization_id = org.id
    async_session.add(
        OrganizationMembershipModel(
            user_id=user.id, organization_id=org.id, role=role.value
        )
    )
    await async_session.flush()
    return user, org


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_name_reaches_the_switcher(async_session):
    from api.app import app
    from api.db import db_client
    from api.services.auth.depends import get_user

    user, org = await _account(
        async_session, name="Narayani Dental", role=OrganizationRole.OWNER
    )
    rows = await db_client.list_user_organizations(user.id)
    assert [(r[0], r[1]) for r in rows] == [(org.id, "Narayani Dental")]

    app.dependency_overrides[get_user] = lambda: user
    try:
        async with _client(app) as client:
            response = await client.get("/api/v1/organizations/mine")
    finally:
        app.dependency_overrides.pop(get_user, None)
    assert response.status_code == 200, response.text
    assert response.json()[0]["name"] == "Narayani Dental"


@pytest.mark.asyncio
async def test_an_unnamed_organisation_shows_its_number(async_session):
    from api.app import app
    from api.services.auth.depends import get_user

    user, org = await _account(async_session, name=None, role=OrganizationRole.OWNER)
    app.dependency_overrides[get_user] = lambda: user
    try:
        async with _client(app) as client:
            response = await client.get("/api/v1/organizations/mine")
    finally:
        app.dependency_overrides.pop(get_user, None)
    assert response.json()[0]["name"] == f"Organization {org.id}"


@pytest.mark.asyncio
async def test_an_admin_can_rename_it(async_session):
    from api.app import app
    from api.db import db_client
    from api.services.auth.depends import get_user_with_selected_organization

    user, org = await _account(async_session, name=None, role=OrganizationRole.ADMIN)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: user
    try:
        async with _client(app) as client:
            response = await client.patch(
                "/api/v1/organizations/selected", json={"name": "  Kriti Labs  "}
            )
    finally:
        app.dependency_overrides.pop(get_user_with_selected_organization, None)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": org.id,
        "name": "Kriti Labs",
        "role": "admin",
        "is_selected": True,
    }
    rows = await db_client.list_user_organizations(user.id)
    assert rows[0][1] == "Kriti Labs"


@pytest.mark.asyncio
async def test_a_member_cannot(async_session):
    from api.app import app
    from api.services.auth.depends import get_user_with_selected_organization

    user, _ = await _account(async_session, name="Before", role=OrganizationRole.MEMBER)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: user
    try:
        async with _client(app) as client:
            response = await client.patch(
                "/api/v1/organizations/selected", json={"name": "After"}
            )
    finally:
        app.dependency_overrides.pop(get_user_with_selected_organization, None)
    assert response.status_code == 403


# The validation below needs no database: the route refuses before it writes.


def _admin():
    return SimpleNamespace(id=7, selected_organization_id=30)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
async def test_a_blank_or_oversized_name_is_refused(name):
    from api.app import app
    from api.services.auth.depends import get_user_with_selected_organization

    app.dependency_overrides[get_user_with_selected_organization] = _admin
    membership = SimpleNamespace(role=OrganizationRole.ADMIN.value)
    try:
        with (
            patch(
                "api.services.auth.depends.db_client.get_membership",
                new=AsyncMock(return_value=membership),
            ),
            patch(
                "api.routes.organization.db_client.rename_organization",
                new=AsyncMock(),
            ) as rename,
        ):
            async with _client(app) as client:
                response = await client.patch(
                    "/api/v1/organizations/selected", json={"name": name}
                )
    finally:
        app.dependency_overrides.pop(get_user_with_selected_organization, None)
    assert response.status_code == 422
    rename.assert_not_awaited()
