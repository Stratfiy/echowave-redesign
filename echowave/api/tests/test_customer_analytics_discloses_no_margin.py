"""What the customer-facing analytics must never contain.

The staff and customer screens read the same queries. Those queries are built
for staff, so they carry what vendors charged us and what we kept — and a route
that passes one straight through publishes our markup to every account that
opens the page, permanently, whether or not any UI renders it.

Money per *named model* is the subtle one. An account's models have public rate
cards, so spend divided by tokens for "gpt-4o-mini" is our price for a model
anyone can look up. The account's own bill, split by component, discloses
nothing of the sort and stays where it is.

These assert on the response the route returns, because the route is the
boundary — not the page. A column removed from a table is one commit away from
coming back; a key that was never in the payload is not.
"""

import pytest

import api.db.billing_dashboard_client as dash
from api.routes import organization_usage

#: Reveal what a component or model cost *us*, or what we kept.
MARGIN_KEYS = {"provider_cost_paise", "margin_paise", "margin_pct", "our_cost_paise"}

#: Reveal a per-unit price. Harmless account-wide; a disclosure beside a model
#: name that has a public rate card.
UNIT_PRICE_KEYS = {"paise_per_1k_tokens", "unit_rate_mpaise", "rate_mpaise"}

FORBIDDEN = MARGIN_KEYS | UNIT_PRICE_KEYS | {"cost_paise"}

#: What the staff queries actually hand back. Fed in verbatim so the test fails
#: if the route ever stops projecting and starts passing through.
STAFF_BY_MODEL = {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "tokens": 4200,
    "cost_paise": 620,
    "calls": 7,
    "tokens_per_call": 600,
    "paise_per_1k_tokens": 14.76,
}

STAFF_SERIES = {
    "period": "2026-08-01",
    "tokens": 4200,
    "cost_paise": 620,
    "calls": 7,
    "minutes": 12.0,
    "tokens_per_minute": 350,
}


class _User:
    selected_organization_id = 1


@pytest.fixture
def staff_rows(monkeypatch):
    """Stub the three staff queries with their real shapes.

    No database: this is a test about which keys survive a projection, and
    standing up Postgres to prove that would only make it slower and flakier.
    """

    async def by_model(*args, **kwargs):
        return [dict(STAFF_BY_MODEL)]

    async def series(*args, **kwargs):
        return [dict(STAFF_SERIES)]

    async def growth(*args, **kwargs):
        return []

    monkeypatch.setattr(dash, "token_usage_by_model", by_model)
    monkeypatch.setattr(dash, "token_usage_series", series)
    monkeypatch.setattr(dash, "context_growth_by_turn", growth)


def _leaks(rows) -> set:
    found = set()
    for row in rows:
        found |= set(row) & FORBIDDEN
    return found


@pytest.mark.asyncio
class TestNoMarginReachesTheCustomer:
    async def test_the_stub_really_does_carry_what_we_are_stripping(self, staff_rows):
        """Guards the guard. If the staff queries ever stop returning these,
        every assertion below would pass while protecting nothing."""
        assert FORBIDDEN & set(STAFF_BY_MODEL)
        assert FORBIDDEN & set(STAFF_SERIES)

    async def test_per_model_rows_carry_no_money(self, staff_rows):
        """Spend beside a named model divides into that model's unit price, and
        the vendor publishes theirs — so the two together are our markup."""
        result = await organization_usage.get_token_usage(
            days=30, granularity="day", user=_User()
        )

        leaked = _leaks(result["by_model"])
        assert not leaked, f"customer by_model exposed {leaked}"

    async def test_the_token_series_carries_no_money(self, staff_rows):
        """One step less obvious: spend and tokens for the same period divide
        into a blended per-token price, and on an account running one model
        blended is that model's price."""
        result = await organization_usage.get_token_usage(
            days=30, granularity="day", user=_User()
        )

        leaked = _leaks(result["series"])
        assert not leaked, f"customer token series exposed {leaked}"

    async def test_the_screen_still_answers_its_question(self, staff_rows):
        """Stripping money must not strip the reason the page exists. Which
        model is burning the context, and would a cheaper one serve, are
        questions answered in tokens."""
        result = await organization_usage.get_token_usage(
            days=30, granularity="day", user=_User()
        )

        row = result["by_model"][0]
        assert row["model"] == "gpt-4o-mini"
        assert row["tokens"] == 4200
        assert row["tokens_per_call"] == 600

        period = result["series"][0]
        assert period["tokens"] == 4200
        assert period["tokens_per_minute"] == 350
        assert period["minutes"] == 12.0
