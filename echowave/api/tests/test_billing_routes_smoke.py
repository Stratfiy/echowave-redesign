"""Every billing GET route answers for a real account, as itself.

The unit suites prove the services compute correctly. This proves the routes
in front of them are wired: mounted at the path they claim, resolving their
dependencies, and serialising a response. A route that 500s on an account with
no payments, no profile and no rate card is the one an operator meets first.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.db.models import OrganizationModel, UserModel

# Customer-facing billing reads. Every one of these is on the path a paying
# account walks, and each is called with an account that has nothing on file --
# the empty state, which is where the crashes live.
CUSTOMER_GETS = [
    "/api/v1/billing/balance",
    "/api/v1/billing/profile",
    "/api/v1/billing/documents",
    "/api/v1/billing/payments",
    "/api/v1/billing/mandate",
    "/api/v1/billing/plan",
]

# Staff billing reads behind get_superuser.
STAFF_GETS = [
    "/api/v1/admin/billing/readiness",
    "/api/v1/admin/billing/rate-card",
    "/api/v1/admin/billing/plans",
    "/api/v1/admin/billing/providers",
    "/api/v1/admin/billing/bundles",
    "/api/v1/admin/billing/bundles/economics",
    "/api/v1/admin/billing/managed-tiers",
    "/api/v1/admin/billing/managed-tiers/choices",
    "/api/v1/admin/billing/pricing-inputs",
]


async def _account(async_session):
    org = OrganizationModel(provider_id="org_smoke")
    async_session.add(org)
    await async_session.flush()
    user = UserModel(provider_id="user_smoke", selected_organization_id=org.id)
    async_session.add(user)
    await async_session.flush()
    return user


@pytest.mark.parametrize("path", CUSTOMER_GETS)
async def test_a_customer_billing_route_answers(async_session, path):
    from api.app import app
    from api.services.auth.depends import get_user

    user = await _account(async_session)
    app.dependency_overrides[get_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(path)
    finally:
        app.dependency_overrides.pop(get_user, None)

    # 200 or a *deliberate* refusal. A 404 means it is not mounted where the UI
    # calls it; a 5xx means the empty state was never exercised.
    assert response.status_code < 500, (
        f"{path} -> {response.status_code} {response.text[:300]}"
    )
    assert response.status_code != 404, f"{path} is not mounted"


@pytest.mark.parametrize("path", STAFF_GETS)
async def test_a_staff_billing_route_answers(async_session, path):
    from api.app import app
    from api.services.auth.depends import get_superuser

    user = await _account(async_session)
    app.dependency_overrides[get_superuser] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(path)
    finally:
        app.dependency_overrides.pop(get_superuser, None)

    assert response.status_code < 500, (
        f"{path} -> {response.status_code} {response.text[:300]}"
    )
    assert response.status_code != 404, f"{path} is not mounted"


@pytest.mark.parametrize("path", CUSTOMER_GETS + STAFF_GETS)
async def test_a_billing_route_refuses_an_anonymous_caller(async_session, path):
    """No billing route answers without authentication.

    Cheap to state and expensive to get wrong: one ungated read here exposes an
    account's balance, its tax identity, or the whole rate card.
    """
    from api.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path)
    assert response.status_code in (401, 403), (
        f"{path} answered an anonymous caller with {response.status_code}"
    )
