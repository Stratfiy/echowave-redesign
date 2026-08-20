"""Changing which vendor serves a managed tier, without a release.

The mapping used to be a constant with an environment-variable escape hatch.
Both are fine for bootstrapping and neither is a product control: moving the
Normal brain to a cheaper model, or pointing speech-to-speech at a vendor who
has just shipped an Indic model, is a decision somebody makes on a Tuesday and
should not need an engineer, a deploy, and a restart of every worker.

What the tests here defend is the part that bites. A tier is not a free-text
field — pointing one at something unbuildable does not fail on save, it fails
at session open, after a customer's call has connected, on every call until
somebody notices.
"""

from __future__ import annotations

import pytest

from api.db.models import ManagedTierMappingModel, UserModel
from api.services.configuration import managed_tiers, tier_admin


@pytest.fixture(autouse=True)
async def _clean_overrides():
    """Each test starts from the compiled defaults.

    The cache is module-level, so a test that left an override in it would
    change what every later test resolves — which is exactly the cross-test
    leak an in-process cache invites.
    """
    managed_tiers._OVERRIDES = {}
    yield
    managed_tiers._OVERRIDES = {}


async def _user(session) -> UserModel:
    user = UserModel(provider_id="tier-admin", email="ops@example.com")
    session.add(user)
    await session.flush()
    return user


class TestResolutionOrder:
    async def test_an_override_beats_the_compiled_default(self):
        before = managed_tiers.resolve("llm", "default")
        managed_tiers._OVERRIDES[("llm", "default")] = managed_tiers.ManagedUpstream(
            "sarvam", "sarvam-105b"
        )
        after = managed_tiers.resolve("llm", "default")

        assert before.provider == "openai"
        assert after.provider == "sarvam"
        assert after.model == "sarvam-105b"

    async def test_clearing_falls_back_to_the_default(self):
        managed_tiers._OVERRIDES[("llm", "default")] = managed_tiers.ManagedUpstream(
            "sarvam", "sarvam-105b"
        )
        managed_tiers._OVERRIDES = {}

        assert managed_tiers.resolve("llm", "default").provider == "openai"

    async def test_a_refresh_replaces_rather_than_merges(
        self, db_session, async_session
    ):
        """A mapping deleted in the database has to disappear from the cache.

        Merging would leave a retired override applying for ever on whichever
        worker happened to be running when it was set — and only that worker,
        which is the worst shape a bug can take.
        """
        managed_tiers._OVERRIDES[("llm", "default")] = managed_tiers.ManagedUpstream(
            "stale", "stale-model"
        )
        await managed_tiers.refresh_overrides()

        assert ("llm", "default") not in managed_tiers._OVERRIDES

    async def test_an_override_moves_what_the_readiness_check_looks_for(self):
        """A tier pointed elsewhere needs a key for the new vendor, not the old.

        Reading only the defaults would report a platform key as present for a
        vendor no tier uses any more, and missing for the one now serving it.
        """
        managed_tiers._OVERRIDES[("tts", "default")] = managed_tiers.ManagedUpstream(
            "elevenlabs", "eleven_flash_v2_5"
        )
        providers = dict(managed_tiers.upstream_providers())

        assert ("tts", "elevenlabs") in managed_tiers.upstream_providers()
        assert ("tts", "sarvam") not in managed_tiers.upstream_providers()


class TestWhatIsRefusedBeforeItBreaksACall:
    async def test_an_unknown_component_is_refused(self, db_session, async_session):
        user = await _user(async_session)
        with pytest.raises(tier_admin.TierMappingError, match="not a component"):
            await tier_admin.set_tier(
                async_session,
                component="telepathy",
                tier="default",
                provider="sarvam",
                model="x",
                actor_user_id=user.id,
            )

    async def test_an_unknown_tier_is_refused(self, db_session, async_session):
        user = await _user(async_session)
        with pytest.raises(tier_admin.TierMappingError, match="not a llm tier"):
            await tier_admin.set_tier(
                async_session,
                component="llm",
                tier="galaxy-brain",
                provider="sarvam",
                model="x",
                actor_user_id=user.id,
            )

    async def test_a_provider_nothing_can_build_is_refused(
        self, db_session, async_session
    ):
        """The failure this prevents happens mid-pipeline-setup, on a live call."""
        user = await _user(async_session)
        with pytest.raises(tier_admin.TierMappingError, match="Nothing can build"):
            await tier_admin.set_tier(
                async_session,
                component="llm",
                tier="default",
                provider="a-vendor-we-never-integrated",
                model="x",
                actor_user_id=user.id,
            )

    async def test_a_blank_provider_or_model_is_refused(
        self, db_session, async_session
    ):
        user = await _user(async_session)
        with pytest.raises(tier_admin.TierMappingError, match="Name both"):
            await tier_admin.set_tier(
                async_session,
                component="llm",
                tier="default",
                provider="openai",
                model="   ",
                actor_user_id=user.id,
            )

    async def test_a_provider_with_no_platform_key_is_refused(
        self, db_session, async_session
    ):
        """Managed resolution would find no credential, log, and leave the
        section unresolved — a tier that looks set and does not work."""
        user = await _user(async_session)
        with pytest.raises(tier_admin.TierMappingError, match="No platform key"):
            await tier_admin.set_tier(
                async_session,
                component="llm",
                tier="default",
                provider="openai",
                model="gpt-4.1-mini",
                actor_user_id=user.id,
            )


class TestListing:
    async def test_every_tier_is_listed_with_what_serves_it(
        self, db_session, async_session
    ):
        views = await tier_admin.list_tiers(async_session)
        by_key = {(v.component, v.tier): v for v in views}

        assert ("llm", "default") in by_key
        assert ("realtime", "natural") in by_key
        assert by_key[("llm", "default")].provider == "openai"
        assert by_key[("llm", "default")].is_override is False

    async def test_a_stored_mapping_is_marked_as_an_override(
        self, db_session, async_session
    ):
        async_session.add(
            ManagedTierMappingModel(
                component="llm", tier="default", provider="sarvam", model="sarvam-105b"
            )
        )
        await async_session.flush()
        await managed_tiers.refresh_overrides()

        views = await tier_admin.list_tiers(async_session)
        row = next(v for v in views if (v.component, v.tier) == ("llm", "default"))

        assert row.is_override is True
        assert row.provider == "sarvam"

    async def test_the_labels_are_the_customer_facing_ones(
        self, db_session, async_session
    ):
        views = await tier_admin.list_tiers(async_session)
        by_key = {(v.component, v.tier): v for v in views}

        assert by_key[("llm", "lite")].label == "Lite"
        assert by_key[("realtime", "natural")].label == "Natural"
        assert by_key[("realtime", "premium")].blurb


class TestWhatATierCanBePointedAt:
    """Read from the registry, not from a list kept beside it.

    A hand-maintained list of "every model we support" goes stale the first
    time somebody adds one — and speech-to-speech vendors ship new models
    faster than anything else in the stack, which is exactly where a stale list
    hurts.
    """

    def test_realtime_offers_the_registered_vendors(self):
        providers = {row["provider"] for row in tier_admin.choices("realtime")}

        assert "openai_realtime" in providers
        assert "google_realtime" in providers

    def test_a_vendor_that_takes_unlisted_models_says_so(self):
        """So the screen offers a text box and a new model does not have to
        wait for a release."""
        rows = {row["provider"]: row for row in tier_admin.choices("realtime")}

        assert rows["openai_realtime"]["allow_custom_model"] is True

    def test_the_models_listed_are_the_ones_the_vendor_serves(self):
        rows = {row["provider"]: row for row in tier_admin.choices("realtime")}

        assert "gemini-3.1-flash-live-preview" in rows["google_realtime"]["models"]

    def test_an_unknown_component_offers_nothing_rather_than_raising(self):
        assert tier_admin.choices("telepathy") == []

    def test_every_component_a_tier_exists_for_has_choices(self):
        """A tier an operator can see and cannot change would be worse than
        one that was never offered."""
        for component in ("llm", "stt", "tts", "realtime", "embeddings"):
            assert tier_admin.choices(component), component
