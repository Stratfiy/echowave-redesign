"""Key in, model on sale, price out — the whole chain.

The operator's path is: install a provider key, read the models that vendor
actually serves, tick the ones we offer, price them on the rate card. The
customer's path is: see exactly those, with what each costs a minute, and use
their own key only for something we do not sell.

Both halves meet at one table, and these tests defend the three ways the join
can be wrong in a way that looks fine:

* **offered but unpriced.** The call works. It bills the platform fee, records
  the provider usage as uncosted, and reports margin we did not earn. So an
  unpriced model must never reach a customer's picker.
* **offered but unkeyed.** Managed resolution finds no credential and the call
  runs on nothing. Same rule.
* **sold twice.** A customer offered a BYOK route to a model we already sell
  managed would pay the vendor directly for something already on their Decibyl
  invoice.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from api.db.models import ProviderRateModel
from api.enums import CostComponent, RateUnit
from api.services.configuration import model_catalogue, model_discovery

LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


def _rate(provider, component, unit, mpaise, model=""):
    return ProviderRateModel(
        provider=provider,
        model=model,
        component=component.value,
        unit=unit.value,
        rate_mpaise=mpaise,
        effective_from=LONG_AGO,
    )


@pytest.fixture
def openai_lists_models(monkeypatch):
    """A vendor that answers, without leaving this process.

    The transport is stubbed rather than the discovery function, so the real
    request building, the real response parsing and the real error handling all
    run. Stubbing `discover` would test the test.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-4.1"},
                    {"id": "gpt-4.1-mini"},
                    {"id": "text-embedding-3-small"},
                    {"id": "whisper-1"},
                    {"id": "some-new-model"},
                ]
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
async def empty_catalogue(async_session):
    """Start from a stated world rather than the seeded one.

    The migration seeds the catalogue with whatever the managed tiers resolve
    to, which is right — those are the models we are already selling — and
    means the table is not empty. A test asserting "there is one row" would
    then be asserting something about the seed rather than about what it did.
    The seed is covered by :class:`TestTheSeededCatalogue`.
    """
    from sqlalchemy import delete

    from api.db.models import PlatformModelModel

    await async_session.execute(delete(PlatformModelModel))
    await async_session.flush()


@pytest.fixture
def has_platform_key(monkeypatch):
    """Pretend the vault holds keys for these ``(component, provider)`` pairs."""

    def _install(pairs: dict[str, list[str]]):
        from api.services.configuration import platform_credentials as creds

        async def _managed_providers(session):
            return pairs

        monkeypatch.setattr(creds, "managed_providers", _managed_providers)

    return _install


class TestAskingTheVendorWhatItServes:
    async def test_the_vendors_list_is_used_when_it_answers(self, openai_lists_models):
        async with openai_lists_models as client:
            found = await model_discovery.discover(
                component="llm",
                provider="openai",
                api_key="sk-test",
                client=client,
            )

        assert found.from_vendor
        assert found.note is None
        ids = [model.id for model in found.models]
        assert "some-new-model" in ids
        # Models this codebase already names come first, so an operator does
        # not hunt through a vendor's alphabet for the one they came for.
        assert ids[0] in model_discovery.known_models("llm", "openai")
        assert next(m for m in found.models if m.id == "gpt-4.1").suggested
        assert not next(m for m in found.models if m.id == "some-new-model").suggested

    async def test_a_vendor_that_cannot_be_reached_falls_back_and_says_so(self):
        """A failure here must not be an empty screen."""

        def refuse(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
            found = await model_discovery.discover(
                component="llm", provider="openai", api_key="sk-test", client=client
            )

        assert not found.from_vendor
        assert found.note is not None and "500" in found.note
        # Still useful: the models we already know about.
        assert [m.id for m in found.models] == list(
            model_discovery.known_models("llm", "openai")
        )

    async def test_a_vendor_with_no_list_endpoint_falls_back_quietly(self):
        found = await model_discovery.discover(
            component="tts", provider="sarvam", api_key="sk-test"
        )

        assert not found.from_vendor
        assert [m.id for m in found.models] == list(
            model_discovery.known_models("tts", "sarvam")
        )

    async def test_no_key_says_so_rather_than_pretending(self):
        found = await model_discovery.discover(
            component="llm", provider="openai", api_key=None
        )

        assert not found.from_vendor
        assert "No key stored" in (found.note or "")

    async def test_google_model_names_lose_their_prefix(self):
        """Google returns "models/gemini-x"; the factory sends the bare id."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["key"] == "goog-test"
            return httpx.Response(
                200,
                json={"models": [{"name": "models/gemini-3.1-flash"}, {"name": "x"}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            found = await model_discovery.discover(
                component="llm",
                provider="google",
                api_key="goog-test",
                client=client,
            )

        assert [m.id for m in found.models] == ["gemini-3.1-flash", "x"]


class TestTheCatalogueIsWhatWeSell:
    @pytest.fixture(autouse=True)
    def _blank(self, empty_catalogue):
        """Each test here states its own catalogue."""

    async def test_a_model_needs_a_key_and_a_price_to_be_sellable(
        self, async_session, has_platform_key
    ):
        has_platform_key({})
        await model_catalogue.set_offered(
            session=async_session,
            component="llm",
            provider="openai",
            models=["gpt-4.1"],
        )

        # No key, no rate: offered, and not sellable.
        [entry] = await model_catalogue.list_catalogue(async_session, component="llm")
        assert entry.enabled and not entry.is_sellable
        assert not entry.has_key and not entry.is_priced
        assert await model_catalogue.sellable(async_session, component="llm") == []

        has_platform_key({"llm": ["openai"]})
        [entry] = await model_catalogue.list_catalogue(async_session, component="llm")
        assert entry.has_key and not entry.is_priced
        assert await model_catalogue.sellable(async_session, component="llm") == []

        async_session.add(
            _rate(
                "openai",
                CostComponent.LLM,
                RateUnit.THOUSAND_TOKENS,
                12_000,
                model="gpt-4.1",
            )
        )
        await async_session.flush()

        [entry] = await model_catalogue.sellable(async_session, component="llm")
        assert entry.is_sellable and not entry.priced_by_fallback

    async def test_a_provider_wide_rate_prices_it_but_says_it_is_approximate(
        self, async_session, has_platform_key
    ):
        """Two models on one provider-wide rate show the same number.

        Real, and worth flagging: it is a mispricing on an expensive model
        sitting beside a cheap default.
        """
        has_platform_key({"tts": ["sarvam"]})
        await model_catalogue.set_offered(
            session=async_session,
            component="tts",
            provider="sarvam",
            models=["bulbul:v2"],
        )
        async_session.add(
            _rate("sarvam", CostComponent.TTS, RateUnit.THOUSAND_CHARS, 40_000)
        )
        await async_session.flush()

        [entry] = await model_catalogue.sellable(async_session, component="tts")
        assert entry.is_priced and entry.priced_by_fallback

    async def test_saving_replaces_rather_than_merges(self, async_session):
        """Unticking a box has to remove the model, or the screen cannot."""
        await model_catalogue.set_offered(
            session=async_session,
            component="llm",
            provider="openai",
            models=["gpt-4.1", "gpt-4.1-mini"],
        )
        await model_catalogue.set_offered(
            session=async_session,
            component="llm",
            provider="openai",
            models=["gpt-4.1-mini"],
        )

        entries = await model_catalogue.list_catalogue(async_session, component="llm")
        assert [entry.model for entry in entries] == ["gpt-4.1-mini"]

    async def test_a_label_is_what_the_customer_reads(self, async_session):
        await model_catalogue.set_offered(
            session=async_session,
            component="llm",
            provider="openai",
            models=["gpt-4.1"],
            labels={"gpt-4.1": "Smart"},
        )
        [entry] = await model_catalogue.list_catalogue(async_session, component="llm")
        assert entry.label == "Smart"

    async def test_an_unnamed_model_shows_its_id(self, async_session):
        await model_catalogue.set_offered(
            session=async_session,
            component="llm",
            provider="openai",
            models=["gpt-4.1"],
        )
        [entry] = await model_catalogue.list_catalogue(async_session, component="llm")
        assert entry.label == "gpt-4.1"

    async def test_the_same_model_twice_is_refused(self, async_session):
        with pytest.raises(model_catalogue.CatalogueError, match="twice"):
            await model_catalogue.set_offered(
                session=async_session,
                component="llm",
                provider="openai",
                models=["gpt-4.1", "gpt-4.1"],
            )

    async def test_a_slot_that_is_not_a_slot_is_refused(self, async_session):
        with pytest.raises(model_catalogue.CatalogueError, match="not a slot"):
            await model_catalogue.set_offered(
                session=async_session,
                component="telepathy",
                provider="openai",
                models=["gpt-4.1"],
            )


class TestWhatACustomersOwnKeyAdds:
    @pytest.fixture(autouse=True)
    def _blank(self, empty_catalogue):
        """Each test here states its own catalogue."""

    async def test_it_is_only_what_we_do_not_provide(self, async_session):
        """The product decision, enforced rather than described.

        We manage the providers. A customer's key is the escape hatch for a
        model we have not curated — never a second way to buy one we sell.
        """
        await model_catalogue.set_offered(
            session=async_session,
            component="llm",
            provider="openai",
            models=["gpt-4.1", "gpt-4.1-mini"],
        )

        we_provide = await model_catalogue.offers(
            async_session, component="llm", provider="openai"
        )
        vendor_list = ["gpt-4.1", "gpt-4.1-mini", "o3-pro", "some-new-model"]
        theirs = [model for model in vendor_list if model not in we_provide]

        assert theirs == ["o3-pro", "some-new-model"]

    async def test_a_disabled_model_is_theirs_to_bring_again(self, async_session):
        """Withdrawing a model from sale hands it back to the escape hatch."""
        await model_catalogue.set_offered(
            session=async_session,
            component="llm",
            provider="openai",
            models=["gpt-4.1"],
        )
        assert await model_catalogue.offers(
            async_session, component="llm", provider="openai"
        ) == {"gpt-4.1"}

        await model_catalogue.set_offered(
            session=async_session, component="llm", provider="openai", models=[]
        )
        assert (
            await model_catalogue.offers(
                async_session, component="llm", provider="openai"
            )
            == set()
        )


class TestThePriceTheCustomerSees:
    @pytest.fixture(autouse=True)
    def _blank(self, empty_catalogue):
        """Each test here states its own catalogue."""

    async def test_every_offered_model_carries_its_own_marked_up_price(
        self, async_session, has_platform_key
    ):
        """The chain's last link: catalogue -> rate card -> markup -> screen."""
        from api.services.configuration.agent_options import catalogue_options

        has_platform_key({"llm": ["openai"]})
        await model_catalogue.set_offered(
            session=async_session,
            component="llm",
            provider="openai",
            models=["gpt-4.1", "gpt-4.1-mini"],
        )
        async_session.add_all(
            [
                _rate(
                    "openai",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    100_000,
                    model="gpt-4.1",
                ),
                _rate(
                    "openai",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    10_000,
                    model="gpt-4.1-mini",
                ),
            ]
        )
        await async_session.flush()

        catalogue = await catalogue_options(async_session, organization_id=None)
        llm = catalogue["llm"]

        assert [option["model"] for option in llm] == ["gpt-4.1-mini", "gpt-4.1"]
        assert all(option["paise_per_minute"] > 0 for option in llm)
        # The dearer model costs more. Obvious, and it is what a picker that
        # showed one provider-wide rate for both would get wrong.
        assert llm[1]["paise_per_minute"] > llm[0]["paise_per_minute"]
        assert not any(option["approximate"] for option in llm)

    async def test_the_catalogue_price_matches_the_estimate_for_the_same_model(
        self, async_session, has_platform_key
    ):
        """One model, two screens, one number.

        The picker prices a model on its own and the cost bar prices it inside a
        stack. Both must come from the same line, or the customer sees one
        figure while choosing and another once chosen.
        """
        from api.services.billing.estimator import estimate_cost_per_minute
        from api.services.configuration.agent_options import catalogue_options

        has_platform_key({"llm": ["openai"]})
        await model_catalogue.set_offered(
            session=async_session,
            component="llm",
            provider="openai",
            models=["gpt-4.1"],
        )
        async_session.add(
            _rate(
                "openai",
                CostComponent.LLM,
                RateUnit.THOUSAND_TOKENS,
                12_000,
                model="gpt-4.1",
            )
        )
        await async_session.flush()

        [option] = (await catalogue_options(async_session, organization_id=None))["llm"]
        estimate = await estimate_cost_per_minute(
            async_session,
            organization_id=None,
            llm_provider="openai",
            llm_model="gpt-4.1",
        )
        line = next(line for line in estimate.lines if line.component == "llm")

        assert option["paise_per_minute"] == line.paise_per_minute

    async def test_an_unpriced_model_never_reaches_the_picker(
        self, async_session, has_platform_key
    ):
        """It would bill the platform fee alone and report margin we did not earn."""
        from api.services.configuration.agent_options import catalogue_options

        has_platform_key({"llm": ["openai"]})
        await model_catalogue.set_offered(
            session=async_session,
            component="llm",
            provider="openai",
            models=["gpt-4.1"],
        )

        catalogue = await catalogue_options(async_session, organization_id=None)

        assert catalogue["llm"] == []


class TestTheSeededCatalogue:
    """What the migration put on sale, which is what we were already selling."""

    async def test_the_managed_tiers_are_in_the_catalogue(self, async_session):
        from api.services.configuration import managed_tiers

        entries = await model_catalogue.list_catalogue(async_session)
        offered = {(entry.component, entry.provider, entry.model) for entry in entries}

        for component, tiers in (
            ("llm", managed_tiers.LLM_TIERS),
            ("stt", managed_tiers.STT_TIERS),
            ("tts", managed_tiers.TTS_TIERS),
        ):
            for tier in tiers:
                upstream = managed_tiers.resolve(component, tier)
                assert (component, upstream.provider, upstream.model) in offered, (
                    f"the {component}/{tier} tier resolves to "
                    f"{upstream.provider}/{upstream.model}, which is not in the "
                    "catalogue — a tier customers are already on must be on sale"
                )
