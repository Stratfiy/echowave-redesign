"""Seen live on a Free account at zero balance: the billing page said
"Calling is paused" and the chip "Too low to place calls", on a plan whose
card, ten lines down, says "Text only: no phone line". The balance now says
whether this account's bots are on the phone, so the screens can name what
actually stops.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import app
from api.services.auth.depends import get_user


async def _balance(*, voice_allowed: bool) -> dict:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=42, selected_organization_id=7
    )
    profile = SimpleNamespace(is_export=False, is_complete=True)
    try:
        with (
            patch("api.routes.payments.db_client.async_session", return_value=session),
            patch(
                "api.routes.payments.payments.current_balance_paise",
                new=AsyncMock(return_value=0),
            ),
            patch("api.routes.payments.is_internal", new=AsyncMock(return_value=False)),
            patch(
                "api.routes.payments.billing_profile.get_profile",
                new=AsyncMock(return_value=profile),
            ),
            patch(
                "api.routes.payments.payments.minimum_topup_paise",
                new=AsyncMock(return_value=10_000),
            ),
            patch(
                "api.routes.payments.plans.pool_balances",
                new=AsyncMock(
                    return_value=SimpleNamespace(plan_paise=0, topup_paise=0)
                ),
            ),
            patch(
                "api.routes.payments.topup_packs.packs_for",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.routes.payments.topup_packs.billing_currency",
                new=AsyncMock(return_value="INR"),
            ),
            patch(
                "api.routes.payments.subscription_plans.plan_for_organization",
                new=AsyncMock(
                    return_value=SimpleNamespace(voice_allowed=voice_allowed)
                ),
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
    return response.json()


class TestTheBalanceSaysWhatItStops:
    @pytest.mark.asyncio
    async def test_a_text_only_plan_is_marked_so(self):
        body = await _balance(voice_allowed=False)
        assert body["calling_blocked"] is True
        assert body["voice_allowed"] is False

    @pytest.mark.asyncio
    async def test_a_voice_plan_is_marked_so(self):
        body = await _balance(voice_allowed=True)
        assert body["voice_allowed"] is True
