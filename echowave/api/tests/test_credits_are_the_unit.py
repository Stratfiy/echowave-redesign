"""One credit is fifty paise, charges round up per event, balances round down.

Arrival tests for KAN-52: the rule lives in one module; a composed call
total is lifted to a whole number of credits before it reaches the ledger;
the balance response says how many credits the account has and what a
credit is; a plan's grant reads as the credits it was sold as.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.services.billing import credits
from api.services.billing.cost_engine import CallCost


class TestTheRule:
    def test_a_charge_rounds_up_per_event(self):
        assert credits.credits_for_charge(0) == 0
        assert credits.credits_for_charge(1) == 1
        assert credits.credits_for_charge(50) == 1
        assert credits.credits_for_charge(51) == 2
        assert credits.credits_for_charge(1730) == 35
        assert credits.round_up_to_credits(1730) == 1750
        assert credits.round_up_to_credits(1750) == 1750

    def test_a_balance_rounds_down(self):
        assert credits.credits_of_balance(1749) == 34
        assert credits.credits_of_balance(1750) == 35
        assert credits.credits_of_balance(250_000) == 5_000  # the Business grant
        assert credits.credits_of_balance(-120) == -2

    def test_the_acceptance_case_a_three_minute_indic_call(self):
        # 12 credits a minute by decision: three minutes compose to Rs 18.
        assert credits.credits_for_charge(1800) == 36


class TestTheEngineChargesWholeCredits:
    def test_a_call_cost_reports_its_credits(self):
        cost = CallCost(
            line_items=(),
            billable_minutes=2,
            platform_rate_mpaise=0,
            platform_fee_paise=0,
            total_provider_cost_paise=300,
            total_charged_paise=550,
        )
        assert cost.credits == 11


@pytest.mark.asyncio
class TestTheBalanceSaysCredits:
    async def test_balance_and_peg_are_in_the_response(self):
        from api.app import app
        from api.services.auth.depends import get_user

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        profile = SimpleNamespace(is_export=False, is_complete=True)
        try:
            with (
                patch(
                    "api.routes.payments.db_client.async_session", return_value=session
                ),
                patch(
                    "api.routes.payments.payments.current_balance_paise",
                    new=AsyncMock(return_value=1749),
                ),
                patch(
                    "api.routes.payments.is_internal", new=AsyncMock(return_value=False)
                ),
                patch(
                    "api.routes.payments.billing_profile.get_profile",
                    new=AsyncMock(return_value=profile),
                ),
                patch(
                    "api.routes.payments.payments.minimum_topup_paise",
                    new=AsyncMock(return_value=10_000),
                ),
                patch(
                    "api.routes.payments.topup_nudge.daily_burn_paise",
                    new=AsyncMock(return_value=0),
                ),
                patch("api.routes.payments.payments.is_configured", return_value=False),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as http:
                    response = await http.get("/api/v1/billing/balance")
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["balance_paise"] == 1749
        assert body["balance_credits"] == 34
        assert body["paise_per_credit"] == 50
        assert body["min_balance_credits"] == credits.credits_for_charge(
            body["min_balance_paise"]
        )
