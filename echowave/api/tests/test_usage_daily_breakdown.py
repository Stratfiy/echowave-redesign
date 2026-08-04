"""The daily usage breakdown for an account with no pricing configured.

This endpoint used to answer 400 for an organization with no
``price_per_second_usd``. The guard was right — there is genuinely nothing to
break down — but the shape was wrong: a dashboard tile rendered an error where
it should render "no data yet", and that is the first screen a new customer
sees.

The distinction the response has to preserve is between *not priced yet* and
*priced, but nobody has called*. Both are empty; only one is something to fix.
"""

import pytest

from api.db.models import OrganizationModel, UserModel


async def _user_in_org(async_session, slug: str, *, price_per_second_usd):
    org = OrganizationModel(
        provider_id=f"org-{slug}",
        quota_decibyl_tokens=0,
        price_per_second_usd=price_per_second_usd,
    )
    async_session.add(org)
    await async_session.flush()

    user = UserModel(provider_id=f"user-{slug}", selected_organization_id=org.id)
    async_session.add(user)
    await async_session.flush()
    return user, org


@pytest.mark.asyncio
class TestAnUnpricedAccount:
    async def test_it_answers_200_rather_than_400(
        self, async_session, db_session, test_client_factory
    ):
        user, _ = await _user_in_org(
            async_session, "unpriced", price_per_second_usd=None
        )

        async with test_client_factory(user) as client:
            response = await client.get(
                "/api/v1/organizations/usage/daily-breakdown?days=7"
            )

        assert response.status_code == 200

    async def test_it_returns_an_empty_series(
        self, async_session, db_session, test_client_factory
    ):
        user, _ = await _user_in_org(async_session, "empty", price_per_second_usd=None)

        async with test_client_factory(user) as client:
            response = await client.get(
                "/api/v1/organizations/usage/daily-breakdown?days=7"
            )

        body = response.json()
        assert body["breakdown"] == []
        assert body["total_minutes"] == 0.0

    async def test_it_says_pricing_is_not_configured(
        self, async_session, db_session, test_client_factory
    ):
        """The flag is the whole point: without it the caller cannot tell an
        unpriced account from a quiet one, and would render the same empty
        chart for a configuration problem and for a normal Sunday."""
        user, _ = await _user_in_org(async_session, "flag", price_per_second_usd=None)

        async with test_client_factory(user) as client:
            response = await client.get(
                "/api/v1/organizations/usage/daily-breakdown?days=7"
            )

        assert response.json()["pricing_configured"] is False

    async def test_no_cost_is_reported_rather_than_a_cost_of_zero(
        self, async_session, db_session, test_client_factory
    ):
        """There is no price, so there is no cost — as distinct from a cost
        that was measured and came to nothing."""
        user, _ = await _user_in_org(async_session, "nocost", price_per_second_usd=None)

        async with test_client_factory(user) as client:
            response = await client.get(
                "/api/v1/organizations/usage/daily-breakdown?days=7"
            )

        body = response.json()
        assert body["total_cost_usd"] is None
        assert body["currency"] is None


@pytest.mark.asyncio
class TestAPricedAccount:
    async def test_a_priced_account_reports_pricing_as_configured(
        self, async_session, db_session, test_client_factory
    ):
        """The default has to stay true for the accounts that were always
        served, or the flag inverts its meaning for every existing caller."""
        user, _ = await _user_in_org(async_session, "priced", price_per_second_usd=0.01)

        async with test_client_factory(user) as client:
            response = await client.get(
                "/api/v1/organizations/usage/daily-breakdown?days=7"
            )

        assert response.status_code == 200
        assert response.json()["pricing_configured"] is True
