"""The Simple picker's cards, as rows an operator owns.

The design decision these tests defend: **a bundle references tiers, not
vendors.** A customer's stored configuration names a tier, so moving a vendor
is one edit that reaches every agent already built. If a bundle named a
provider and model directly, changing one would leave every existing agent on
the old vendor and every new one on the new — the drift tiers exist to prevent.

So the two edits are different jobs, and the tests below check that the split
holds: a bundle edit changes what a card is called and whether it is offered;
what it runs on comes from resolving its tiers.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from api.db.models import ManagedBundleModel, UserModel
from api.services.configuration import bundles, managed_tiers


@pytest.fixture(autouse=True)
def _clean_tier_overrides():
    managed_tiers._OVERRIDES = {}
    yield
    managed_tiers._OVERRIDES = {}


async def _user(session) -> UserModel:
    user = UserModel(provider_id="bundle-admin", email="ops@example.com")
    session.add(user)
    await session.flush()
    return user


class TestSeeding:
    async def test_the_shipped_bundles_appear_on_first_read(
        self, db_session, async_session
    ):
        rows = await bundles.list_bundles(async_session)
        slugs = [row.slug for row in rows]

        assert slugs == ["everyday", "natural", "premium"]

    async def test_seeding_is_idempotent(self, db_session, async_session):
        await bundles.list_bundles(async_session)
        await bundles.list_bundles(async_session)

        count = len((await async_session.scalars(select(ManagedBundleModel))).all())
        assert count == 3

    async def test_a_deleted_bundle_does_not_come_back(self, db_session, async_session):
        """Seeding only fills an empty table. An operator who removed a bundle
        on purpose must not have it reappear on the next request."""
        await bundles.list_bundles(async_session)
        row = await async_session.scalar(
            select(ManagedBundleModel).where(ManagedBundleModel.slug == "premium")
        )
        await async_session.delete(row)
        await async_session.flush()

        slugs = [r.slug for r in await bundles.list_bundles(async_session)]
        assert "premium" not in slugs


class TestWhatABundleRunsOn:
    async def test_slots_resolve_through_the_tier(self, db_session, async_session):
        rows = await bundles.list_bundles(async_session)
        natural = next(r for r in rows if r.slug == "natural")
        slots = bundles.resolved_slots(natural)

        realtime = next(s for s in slots if s["component"] == "realtime")
        assert realtime["tier"] == "natural"
        assert realtime["provider"] == "google_realtime"

    async def test_changing_the_tier_changes_every_bundle_using_it(
        self, db_session, async_session
    ):
        """The reason for the indirection, stated as a test. One tier edit, and
        the bundle follows — including for agents already built on it."""
        rows = await bundles.list_bundles(async_session)
        natural = next(r for r in rows if r.slug == "natural")

        managed_tiers._OVERRIDES[("realtime", "natural")] = (
            managed_tiers.ManagedUpstream("openai_realtime", "gpt-realtime-2")
        )
        slots = bundles.resolved_slots(natural)
        realtime = next(s for s in slots if s["component"] == "realtime")

        assert realtime["provider"] == "openai_realtime"

    async def test_the_brain_the_customer_picks_is_marked_not_missing(
        self, db_session, async_session
    ):
        """An empty cell reads as a misconfiguration. This one is a choice."""
        rows = await bundles.list_bundles(async_session)
        everyday = next(r for r in rows if r.slug == "everyday")
        slots = bundles.resolved_slots(everyday)

        llm = next(s for s in slots if s["component"] == "llm")
        assert llm["chosen_by_customer"] is True
        assert llm["provider"] is None

    async def test_a_realtime_bundle_has_no_pipeline_slots(
        self, db_session, async_session
    ):
        rows = await bundles.list_bundles(async_session)
        premium = next(r for r in rows if r.slug == "premium")
        components = {s["component"] for s in bundles.resolved_slots(premium)}

        assert components == {"realtime"}


class TestEditing:
    async def test_a_bundle_can_be_renamed_without_changing_what_it_runs(
        self, db_session, async_session
    ):
        user = await _user(async_session)
        await bundles.list_bundles(async_session)

        row = await bundles.upsert_bundle(
            async_session,
            slug="everyday",
            actor_user_id=user.id,
            label="Standard",
            blurb="Our workhorse.",
        )

        assert row.label == "Standard"
        assert row.stt_tier == "default"
        assert row.architecture == bundles.PIPELINE

    async def test_a_bundle_can_be_hidden_rather_than_deleted(
        self, db_session, async_session
    ):
        """An agent already running on it must keep resolving."""
        user = await _user(async_session)
        await bundles.list_bundles(async_session)

        await bundles.upsert_bundle(
            async_session, slug="premium", actor_user_id=user.id, is_enabled=False
        )

        offered = [
            r.slug for r in await bundles.list_bundles(async_session, enabled_only=True)
        ]
        everything = [r.slug for r in await bundles.list_bundles(async_session)]
        assert "premium" not in offered
        assert "premium" in everything

    async def test_a_new_bundle_can_be_added(self, db_session, async_session):
        user = await _user(async_session)
        await bundles.list_bundles(async_session)

        await bundles.upsert_bundle(
            async_session,
            slug="budget",
            actor_user_id=user.id,
            label="Budget",
            blurb="Cheapest we can do.",
            architecture=bundles.PIPELINE,
            stt_tier="default",
            tts_tier="default",
            llm_tier="lite",
            display_order=5,
        )

        slugs = [r.slug for r in await bundles.list_bundles(async_session)]
        assert slugs[0] == "budget"  # display_order 5 sorts first

    async def test_switching_a_bundle_to_speech_to_speech_clears_the_pipeline(
        self, db_session, async_session
    ):
        """The edit that a naive setter makes impossible: the tiers that no
        longer apply have to be clearable, and validation insists they are."""
        user = await _user(async_session)
        await bundles.list_bundles(async_session)

        row = await bundles.upsert_bundle(
            async_session,
            slug="everyday",
            actor_user_id=user.id,
            architecture=bundles.REALTIME,
            realtime_tier="natural",
            stt_tier=None,
            tts_tier=None,
            llm_tier=None,
        )

        assert row.architecture == bundles.REALTIME
        assert row.stt_tier is None


class TestWhatIsRefused:
    async def test_a_pipeline_bundle_without_speech_is_refused(
        self, db_session, async_session
    ):
        user = await _user(async_session)
        with pytest.raises(bundles.BundleError, match="transcriber tier"):
            await bundles.upsert_bundle(
                async_session,
                slug="broken",
                actor_user_id=user.id,
                label="Broken",
                architecture=bundles.PIPELINE,
            )

    async def test_a_realtime_bundle_with_pipeline_tiers_is_refused(
        self, db_session, async_session
    ):
        user = await _user(async_session)
        with pytest.raises(bundles.BundleError, match="one model for the whole"):
            await bundles.upsert_bundle(
                async_session,
                slug="confused",
                actor_user_id=user.id,
                label="Confused",
                architecture=bundles.REALTIME,
                realtime_tier="natural",
                stt_tier="default",
            )

    async def test_an_unknown_tier_is_refused(self, db_session, async_session):
        user = await _user(async_session)
        with pytest.raises(bundles.BundleError, match="is not a llm tier"):
            await bundles.upsert_bundle(
                async_session,
                slug="everyday",
                actor_user_id=user.id,
                llm_tier="galaxy-brain",
            )

    async def test_an_unknown_architecture_is_refused(self, db_session, async_session):
        user = await _user(async_session)
        with pytest.raises(bundles.BundleError, match="not an architecture"):
            await bundles.upsert_bundle(
                async_session,
                slug="weird",
                actor_user_id=user.id,
                label="Weird",
                architecture="telepathy",
            )

    async def test_a_bundle_needs_a_name_customers_can_read(
        self, db_session, async_session
    ):
        user = await _user(async_session)
        with pytest.raises(bundles.BundleError, match="name customers can read"):
            await bundles.upsert_bundle(
                async_session,
                slug="nameless",
                actor_user_id=user.id,
                label="  ",
                architecture=bundles.REALTIME,
                realtime_tier="natural",
            )
