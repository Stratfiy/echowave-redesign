"""The partner routes, and who may reach them.

The service tests cover what the rules are. This covers the half that only
shows up once the rules are behind HTTP: that the customer half is reachable
by an ordinary member, that the staff half is not reachable by a customer at
all, and that the customer never sees the rate card their commission is
computed against.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.app import app
from api.db.models import OrganizationMembershipModel, OrganizationModel, UserModel
from api.enums import CommissionBasis, OrganizationRole, PartnerKind
from api.services.auth.depends import get_superuser, get_user

STAFF_ROUTES = [
    ("GET", "/api/v1/admin/partners/queue"),
    ("POST", "/api/v1/admin/partners/1/approve"),
    ("POST", "/api/v1/admin/partners/1/reject"),
    ("GET", "/api/v1/admin/partners/accounts/1/commission"),
    ("PUT", "/api/v1/admin/partners/accounts/1/commission"),
]


async def _member(async_session, slug: str) -> UserModel:
    """An ordinary member of an ordinary account — no staff role at all."""
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id=f"user-{slug}",
        email=f"{slug}@example.com",
        selected_organization_id=org.id,
        staff_role=None,
    )
    async_session.add(user)
    await async_session.flush()
    async_session.add(
        OrganizationMembershipModel(
            user_id=user.id,
            organization_id=org.id,
            role=OrganizationRole.MEMBER.value,
        )
    )
    await async_session.flush()
    return user


def _client(user: UserModel) -> AsyncClient:
    app.dependency_overrides[get_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
class TestTheCustomerHalfIsOpen:
    async def test_a_plain_member_can_apply(self, db_session, async_session):
        """Deliberately not admin-gated. Asking is not spending, and the
        person at an agency who notices we have a partner programme is rarely
        the one holding the billing profile."""
        user = await _member(async_session, "apply-member")

        async with _client(user) as client:
            response = await client.post(
                "/api/v1/partners/application",
                json={
                    "kind": PartnerKind.AGENCY.value,
                    "expected_minutes_per_month": 4000,
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["application"]["status"] == "pending"

    async def test_a_second_submit_is_a_400_not_a_500(self, db_session, async_session):
        """A double click must read as a sentence, not a constraint violation."""
        user = await _member(async_session, "apply-twice")

        async with _client(user) as client:
            first = await client.post(
                "/api/v1/partners/application",
                json={"kind": PartnerKind.RESELLER.value},
            )
            second = await client.post(
                "/api/v1/partners/application",
                json={"kind": PartnerKind.RESELLER.value},
            )

        assert first.status_code == 200
        assert second.status_code == 400
        assert "already have an application" in second.json()["detail"]

    async def test_an_account_that_never_applied_reads_as_empty(
        self, db_session, async_session
    ):
        """The screen has to render before there is anything to render."""
        user = await _member(async_session, "never-applied")

        async with _client(user) as client:
            response = await client.get("/api/v1/partners/application")

        body = response.json()
        assert response.status_code == 200
        assert body["application"] is None
        assert body["commission"] is None
        assert set(body["kinds"]) == {k.value for k in PartnerKind}

    async def test_the_partner_sees_the_rate_but_not_the_rate_card(
        self, db_session, async_session
    ):
        """A partner may see what they are paid — their own contract — and not
        what it is computed against. Returning the provider cost or the margin
        here would put the whole rate card behind one commission lookup."""
        from api.services.partners import applications as partners

        user = await _member(async_session, "sees-rate")
        await partners.set_commission(
            async_session,
            organization_id=user.selected_organization_id,
            commission_bps=1250,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
        )
        await async_session.commit()

        async with _client(user) as client:
            response = await client.get("/api/v1/partners/application")

        body = response.json()
        assert body["commission"]["commission_bps"] == 1250
        for leak in ("provider_cost", "margin", "platform_rate", "mpaise"):
            assert leak not in response.text


@pytest.mark.asyncio
class TestTheStaffHalfIsShut:
    @pytest.mark.parametrize("method,path", STAFF_ROUTES)
    async def test_a_customer_cannot_reach_it(
        self, db_session, async_session, method, path
    ):
        """Granting a commission is spending our money.

        The status is 401 rather than 403 because `_require_staff_role` calls
        `get_user` as a plain function rather than as a dependency, so a test
        override of `get_user` never reaches it and the request arrives
        tokenless. Both codes are refusals and either is fine here; what must
        never appear is a 200, or a 422 from body validation — a 422 would mean
        the request body was parsed before anyone checked who was asking.
        """
        user = await _member(async_session, f"shut-{method}-{path.count('/')}")

        async with _client(user) as client:
            response = await client.request(method, path, json={})

        assert response.status_code in (401, 403), (
            f"{method} {path} -> {response.status_code}"
        )

    @pytest.mark.parametrize("method,path", STAFF_ROUTES)
    async def test_staff_can_reach_it(self, db_session, async_session, method, path):
        """The other half of the pair.

        Without this, every refusal above would also pass against a router that
        was simply broken — a typo'd path 404s for a customer just as
        effectively as a guard does.
        """
        staff = await _member(async_session, f"open-{method}-{path.count('/')}")
        app.dependency_overrides[get_superuser] = lambda: staff

        async with _client(staff) as client:
            response = await client.request(method, path, json={})

        # 404 for the fabricated ids, 422 for the empty bodies — both mean the
        # guard let us through and the handler ran. Only 401/403 would not.
        assert response.status_code not in (401, 403), (
            f"{method} {path} -> {response.status_code}"
        )


def test_the_app_is_the_real_one():
    """Guards the whole file: overriding `get_user` on a stub app would make
    every assertion above vacuous."""
    assert isinstance(app, FastAPI)
    assert get_superuser is not get_user


def test_every_admin_partner_route_is_guarded():
    """A route added to this prefix without the router-level dependency
    would be staff-shaped and unguarded, which is the failure a per-route
    decorator invites and a router-level one does not.

    Compared by identity rather than by name: the guard is a closure built
    by `_require_staff_role`, so every staff dependency is called
    `_dependency` and a name check would pass for `get_staff` too — a
    strictly wider grant than this router should carry.
    """
    paths = {
        r.path
        for r in app.routes
        if getattr(r, "path", "").startswith("/api/v1/admin/partners")
    }
    assert paths, "no admin partner routes registered"

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/admin/partners"):
            continue
        calls = {d.call for d in route.dependant.dependencies}
        for dependency in route.dependant.dependencies:
            calls |= {s.call for s in dependency.dependencies}
        assert get_superuser in calls, path
