"""What is left in the accounts our keys draw on.

The thing worth pinning down here is not the parsing — it is the refusal to
guess. A balance with no currency behind it never reports ``low``, an
unreachable vendor never reports ``empty``, and a provider with no balance API
says so rather than showing a blank that reads as zero.
"""

from datetime import UTC, datetime

import httpx
import pytest

from api.services.configuration import provider_balance as pb


def _client(handler) -> httpx.AsyncClient:
    """An AsyncClient wired to answer from a function instead of the network."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestMoneyClassification:
    def test_zero_is_empty(self):
        assert pb._classify_money(0.0, "usd") == "empty"

    def test_negative_is_empty(self):
        assert pb._classify_money(-3.0, "usd") == "empty"

    def test_below_the_floor_is_low(self):
        assert pb._classify_money(10.0, "usd") == "low"

    def test_above_the_floor_is_ok(self):
        assert pb._classify_money(500.0, "usd") == "ok"

    def test_currency_case_does_not_matter(self):
        assert pb._classify_money(10.0, "USD") == "low"

    def test_a_currency_we_have_no_floor_for_is_never_low(self):
        # The whole point: a bare 10 in an unknown currency is not evidence of
        # anything. Only "there is none left" survives without a denominator.
        assert pb._classify_money(10.0, "eur") == "ok"
        assert pb._classify_money(10.0, None) == "ok"

    def test_empty_still_applies_without_a_currency(self):
        assert pb._classify_money(0.0, None) == "empty"


class TestQuotaClassification:
    def test_fully_spent_is_empty(self):
        assert pb._classify_quota(1000.0, 1000.0) == "empty"

    def test_overspent_is_empty(self):
        assert pb._classify_quota(1200.0, 1000.0) == "empty"

    def test_last_tenth_is_low(self):
        assert pb._classify_quota(950.0, 1000.0) == "low"

    def test_plenty_left_is_ok(self):
        assert pb._classify_quota(100.0, 1000.0) == "ok"

    def test_no_ceiling_makes_no_claim(self):
        # Nothing to measure against, so no verdict is invented.
        assert pb._classify_quota(500.0, 0.0) == "ok"


class TestNumberParsing:
    def test_reads_a_string(self):
        assert pb._number("12.5") == 12.5

    def test_reads_a_number(self):
        assert pb._number(3) == 3.0

    def test_none_stays_none(self):
        assert pb._number(None) is None

    def test_nonsense_is_none(self):
        assert pb._number("plenty") is None

    def test_a_bool_is_not_a_balance(self):
        # True would otherwise arrive as 1.0 and read as a balance of one.
        assert pb._number(True) is None


class TestElevenLabs:
    @pytest.mark.asyncio
    async def test_reads_the_character_quota(self):
        def handler(request):
            assert request.headers["xi-api-key"] == "key"
            return httpx.Response(
                200,
                json={
                    "character_count": 90_000,
                    "character_limit": 100_000,
                    "next_character_count_reset_unix": 1_760_000_000,
                    "tier": "creator",
                },
            )

        async with _client(handler) as client:
            balance = await pb._elevenlabs(client, "key")

        assert balance.kind == "quota"
        assert balance.used == 90_000
        assert balance.limit == 100_000
        assert balance.remaining == 10_000
        # Ten per cent left is exactly the line, and the line is exclusive.
        assert balance.status == "ok"
        assert balance.renews_at == datetime.fromtimestamp(1_760_000_000, UTC)
        assert "creator" in balance.detail

    @pytest.mark.asyncio
    async def test_a_spent_quota_is_empty(self):
        def handler(request):
            return httpx.Response(
                200, json={"character_count": 100_000, "character_limit": 100_000}
            )

        async with _client(handler) as client:
            balance = await pb._elevenlabs(client, "key")

        assert balance.status == "empty"
        assert balance.remaining == 0
        assert balance.renews_at is None

    @pytest.mark.asyncio
    async def test_a_body_without_a_count_is_unreachable_not_empty(self):
        # A vendor changing its response shape must not read as "no credit".
        def handler(request):
            return httpx.Response(200, json={"tier": "creator"})

        async with _client(handler) as client:
            balance = await pb._elevenlabs(client, "key")

        assert balance.status == "unreachable"
        assert balance.remaining is None


class TestDeepgram:
    @pytest.mark.asyncio
    async def test_sums_every_balance_on_the_project(self):
        def handler(request):
            if request.url.path == "/v1/projects":
                return httpx.Response(200, json={"projects": [{"project_id": "p1"}]})
            assert request.url.path == "/v1/projects/p1/balances"
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {"amount": 40.0, "units": "usd"},
                        {"amount": 25.5, "units": "usd"},
                    ]
                },
            )

        async with _client(handler) as client:
            balance = await pb._deepgram(client, "key")

        assert balance.kind == "money"
        # Promotional credit alongside paid credit: the first row alone would
        # have said 40 and called it low.
        assert balance.amount == 65.5
        assert balance.currency == "usd"
        assert balance.status == "ok"

    @pytest.mark.asyncio
    async def test_a_drained_project_is_empty(self):
        def handler(request):
            if request.url.path == "/v1/projects":
                return httpx.Response(200, json={"projects": [{"project_id": "p1"}]})
            return httpx.Response(
                200, json={"balances": [{"amount": 0, "units": "usd"}]}
            )

        async with _client(handler) as client:
            balance = await pb._deepgram(client, "key")

        assert balance.status == "empty"

    @pytest.mark.asyncio
    async def test_no_projects_is_unreachable(self):
        def handler(request):
            return httpx.Response(200, json={"projects": []})

        async with _client(handler) as client:
            balance = await pb._deepgram(client, "key")

        assert balance.status == "unreachable"


class TestCarriers:
    @pytest.mark.asyncio
    async def test_plivo_credits_carry_no_currency(self):
        def handler(request):
            assert request.url.path == "/v1/Account/AUTHID/"
            return httpx.Response(
                200, json={"cash_credits": "1250.75", "name": "Decibyl"}
            )

        async with _client(handler) as client:
            balance = await pb._plivo(client, "AUTHID", "token")

        assert balance.amount == 1250.75
        # Plivo does not say whether that is rupees or dollars, so no threshold
        # is applied to it — an INR floor on a USD account would be out by
        # nearly two orders of magnitude.
        assert balance.currency is None
        assert balance.status == "ok"

    @pytest.mark.asyncio
    async def test_plivo_at_zero_is_still_empty(self):
        def handler(request):
            return httpx.Response(200, json={"cash_credits": "0"})

        async with _client(handler) as client:
            balance = await pb._plivo(client, "AUTHID", "token")

        assert balance.status == "empty"

    @pytest.mark.asyncio
    async def test_twilio_reports_its_currency(self):
        def handler(request):
            return httpx.Response(200, json={"balance": "12.00", "currency": "USD"})

        async with _client(handler) as client:
            balance = await pb._twilio(client, "SID", "token")

        assert balance.amount == 12.0
        assert balance.currency == "usd"
        assert balance.status == "low"


class TestSupport:
    def test_the_vendors_that_answer(self):
        assert pb.can_read_balance("elevenlabs")
        assert pb.can_read_balance("deepgram")

    def test_the_vendors_that_do_not(self):
        assert not pb.can_read_balance("openai")
        assert not pb.can_read_balance("anthropic")

    @pytest.mark.asyncio
    async def test_a_provider_with_no_balance_api_says_why(self):
        balance = await pb.read_model_provider_balance("openai", "key")
        assert balance.status == "unsupported"
        assert "no balance endpoint" in balance.detail
        assert balance.amount is None

    @pytest.mark.asyncio
    async def test_an_unknown_provider_is_unsupported_not_an_error(self):
        balance = await pb.read_model_provider_balance("nobody", "key")
        assert balance.status == "unsupported"

    @pytest.mark.asyncio
    async def test_a_vendor_rejection_is_unreachable_not_empty(self, monkeypatch):
        async def rejects(client, api_key):
            raise httpx.HTTPStatusError(
                "401",
                request=httpx.Request("GET", "https://example.test"),
                response=httpx.Response(401),
            )

        monkeypatch.setitem(pb.MODEL_PROVIDERS, "deepgram", rejects)
        balance = await pb.read_model_provider_balance("deepgram", "key")

        assert balance.status == "unreachable"
        assert "401" in balance.detail
        assert not balance.needs_attention

    @pytest.mark.asyncio
    async def test_a_transport_failure_is_unreachable(self, monkeypatch):
        async def explodes(client, api_key):
            raise httpx.ConnectError("no route")

        monkeypatch.setitem(pb.MODEL_PROVIDERS, "deepgram", explodes)
        balance = await pb.read_model_provider_balance("deepgram", "key")

        assert balance.status == "unreachable"


class TestNeedsAttention:
    def test_only_low_and_empty_ask_for_action(self):
        assert pb.ProviderBalance("x", "low").needs_attention
        assert pb.ProviderBalance("x", "empty").needs_attention
        assert not pb.ProviderBalance("x", "ok").needs_attention
        # A vendor having a bad minute is not a reason to top anything up.
        assert not pb.ProviderBalance("x", "unreachable").needs_attention
        assert not pb.ProviderBalance("x", "unsupported").needs_attention
        assert not pb.ProviderBalance("x", "unconfigured").needs_attention


class TestReadAll:
    @pytest.mark.asyncio
    async def test_reports_every_account_even_when_none_are_configured(
        self, monkeypatch
    ):
        async def no_key(session, *, component, provider):
            return None

        monkeypatch.setattr(pb.platform_credentials, "resolve_api_key", no_key)
        monkeypatch.setattr(pb, "PLATFORM_PLIVO_AUTH_ID", None)
        monkeypatch.setattr(pb, "PLATFORM_PLIVO_AUTH_TOKEN", None)
        monkeypatch.setattr(pb, "PLATFORM_TWILIO_ACCOUNT_SID", None)
        monkeypatch.setattr(pb, "PLATFORM_TWILIO_AUTH_TOKEN", None)

        balances = await pb.read_all(session=None)

        assert {b.provider for b in balances} == {
            "elevenlabs",
            "deepgram",
            "plivo",
            "twilio",
        }
        # Nothing configured is not the same as nothing left.
        assert all(b.status == "unconfigured" for b in balances)
        assert not any(b.needs_attention for b in balances)

    @pytest.mark.asyncio
    async def test_one_dead_vendor_does_not_take_the_report_with_it(self, monkeypatch):
        async def key(session, *, component, provider):
            return "k" if provider == "deepgram" else None

        async def explodes(client, api_key):
            raise RuntimeError("boom")

        monkeypatch.setattr(pb.platform_credentials, "resolve_api_key", key)
        monkeypatch.setitem(pb.MODEL_PROVIDERS, "deepgram", explodes)
        monkeypatch.setattr(pb, "PLATFORM_PLIVO_AUTH_ID", None)
        monkeypatch.setattr(pb, "PLATFORM_PLIVO_AUTH_TOKEN", None)
        monkeypatch.setattr(pb, "PLATFORM_TWILIO_ACCOUNT_SID", None)
        monkeypatch.setattr(pb, "PLATFORM_TWILIO_AUTH_TOKEN", None)

        balances = await pb.read_all(session=None)

        by_provider = {b.provider: b for b in balances}
        assert by_provider["deepgram"].status == "unreachable"
        assert len(balances) == 4
