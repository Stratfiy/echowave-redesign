"""What the customer-facing analytics must never contain.

The staff and customer screens read the same queries. Those queries are built
for staff, so they carry what vendors charged us and what we kept — and a route
that passes one straight through publishes our markup to every account that
opens the page, permanently, whether or not any UI renders it.

This file used to prove that about the customer token route by feeding it
staff-shaped rows and checking which keys survived the projection. That route
is gone — removed outright rather than sanitised, because tokens divided into
the spend on the next tab give a blended per-token price on their own. Its
absence is asserted in ``test_token_analytics_is_staff_only.py``.

What is left needs the guard more, not less. ``/usage/spend`` returns
``cost_composition_series`` **verbatim** — the one customer analytics route
that does not re-project — so the boundary there is a property of the query
rather than of the route, and nothing fails if the query grows a column. It is
safe today for a reason worth writing down: ``call_cost_items.cost_paise`` is
what the *customer was charged*, and the vendor's figure lives in a separate
``provider_cost_paise`` the query never selects.

These assert on the response the route returns, because the route is the
boundary — not the page. A column removed from a table is one commit away from
coming back; a key that was never in the payload is not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.enums import CostComponent
from api.tests.test_customer_usage_analytics import _account, _client, _costed_call

#: Reveal what a component or model cost *us*, or what we kept.
MARGIN_KEYS = {"provider_cost_paise", "margin_paise", "margin_pct", "our_cost_paise"}

#: Reveal a per-unit price. Harmless account-wide; a disclosure beside a model
#: name that has a public rate card.
UNIT_PRICE_KEYS = {"paise_per_1k_tokens", "unit_rate_mpaise", "rate_mpaise"}

FORBIDDEN = MARGIN_KEYS | UNIT_PRICE_KEYS

#: What a day of spend is allowed to be split into. Named positively on
#: purpose: a deny-list only catches the leak somebody already thought of, and
#: the failure this file exists to prevent is a *new* column arriving in a
#: staff query and riding the pass-through out.
ALLOWED_SPEND_KEYS = {"day", "stt", "llm", "tts", "telephony", "platform"}


@pytest.mark.asyncio
class TestNoMarginReachesTheCustomer:
    async def test_the_spend_series_is_only_what_the_customer_paid(
        self, db_session, async_session
    ):
        """The pass-through route, pinned by an allow-list.

        ``/usage/spend`` hands ``cost_composition_series`` back untouched. That
        is fine while the query selects the charged figure and nothing else —
        and this is what notices if it ever selects more.
        """
        org, user = await _account(async_session, "spend-keys", balance_paise=50_000)
        await _costed_call(
            async_session,
            org,
            user,
            when=datetime.now(UTC) - timedelta(days=1),
            items=[
                (CostComponent.LLM.value, "openai", "gpt-4o", 500, 1000),
                (CostComponent.TTS.value, "sarvam", "bulbul:v2", 300, 900),
            ],
        )

        async with _client(user) as client:
            response = await client.get("/api/v1/organizations/usage/spend")

        assert response.status_code == 200
        series = response.json()["series"]
        assert series, "the fixture placed a costed call in the window"

        unexpected = set()
        for row in series:
            unexpected |= set(row) - ALLOWED_SPEND_KEYS
        assert not unexpected, (
            "the spend series grew keys the customer should not see. "
            "/usage/spend returns cost_composition_series verbatim, so a new "
            f"column in that query reaches every account: {sorted(unexpected)}"
        )

    async def test_no_customer_analytics_route_names_a_margin_key(
        self, db_session, async_session
    ):
        """Across every surviving customer analytics route at once.

        Asserted on the raw body rather than on parsed keys, so a margin figure
        nested inside a row — or inside a totals object added later — is caught
        as readily as a top-level one.
        """
        org, user = await _account(async_session, "margin-sweep", balance_paise=50_000)
        await _costed_call(
            async_session,
            org,
            user,
            items=[(CostComponent.STT.value, "sarvam", "saarika:v2.5", 400, 60)],
        )

        async with _client(user) as client:
            for path in ("usage/spend", "usage/calls"):
                response = await client.get(f"/api/v1/organizations/{path}")
                assert response.status_code == 200, path
                leaked = {key for key in FORBIDDEN if key in response.text}
                assert not leaked, f"/{path} exposed {sorted(leaked)}"
