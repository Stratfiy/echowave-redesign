"""Saving a Simple choice, and what the screen is allowed to save.

The Simple picker used to have no Save at all: it reported the choice to a
parent through a callback, and on the Models screen — where nothing is
listening — clicking a bundle changed the highlight and nothing else. The
account went on dialling whatever it dialled before, on a screen that looked
like settings.

Two things are worth defending here and neither is the button.

**One store, two vocabularies.** Simple and Advanced describe the same account
default. If Simple wrote somewhere else, the two screens would disagree the
first time anybody used both, and there is no correct way to reconcile them
afterwards.

**The client names a bundle, not a stack.** Everything else is resolved from
the bundle row server-side. A request that could name its own STT tier could
name one nobody has priced, and the first anyone would know is a call costed
against a rate that does not exist.
"""

from __future__ import annotations

import pytest

from api.db.models import OrganizationModel
from api.schemas.ai_model_configuration import compile_ai_model_configuration_v2
from api.services.configuration import agent_options
from api.services.configuration import bundles as bundle_service
from api.services.configuration.ai_model_configuration import (
    get_organization_ai_model_configuration_v2,
)


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


@pytest.fixture
async def seeded(async_session):
    await bundle_service.ensure_seeded(async_session)


class TestSavingReachesTheStackTheCallWillRun:
    async def test_a_pipeline_choice_becomes_the_accounts_managed_stack(
        self, db_session, async_session, seeded
    ):
        org = await _org(async_session, "pipeline-save")
        await async_session.commit()

        await agent_options.save_bundle_selection(
            async_session,
            organization_id=org.id,
            bundle_slug="everyday",
            tier="accurate",
            voice=agent_options.voices()[0].voice_id,
        )

        stored = await get_organization_ai_model_configuration_v2(org.id)
        assert stored is not None
        assert stored.mode == "decibyl"
        assert stored.decibyl.llm_tier == "accurate"

        # The tier reaches the compiled stack the pipeline consumes, which is
        # the only thing that makes the click matter. A stored field nothing
        # compiles is a preference, not a configuration.
        effective = compile_ai_model_configuration_v2(stored)
        assert effective.is_realtime is False
        assert effective.llm.model == "accurate"

    async def test_the_speech_tiers_come_from_the_bundle_not_the_request(
        self, db_session, async_session, seeded
    ):
        """The client sends three fields. The stack has five.

        The rest are looked up, so a bundle whose STT tier an operator moves
        reaches every account that saved it — which is the entire reason a
        bundle names tiers rather than vendors.
        """
        org = await _org(async_session, "speech-tiers")
        await async_session.commit()
        row = next(
            r
            for r in await bundle_service.list_bundles(async_session, enabled_only=True)
            if r.slug == "everyday"
        )

        await agent_options.save_bundle_selection(
            async_session,
            organization_id=org.id,
            bundle_slug="everyday",
            tier="default",
            voice="",
        )

        stored = await get_organization_ai_model_configuration_v2(org.id)
        assert stored.decibyl.stt_tier == (row.stt_tier or "default")
        assert stored.decibyl.tts_tier == (row.tts_tier or "default")

    async def test_a_speech_to_speech_bundle_saves_as_realtime(
        self, db_session, async_session, seeded
    ):
        """A realtime model replaces the transcriber and the voice.

        Managed mode had no way to say that at the account level — the only
        managed shape was a pipeline — so choosing Natural or Premium on the
        Simple tab and saving it would have stored a cascade, and the account
        would have run a stack it did not pick.
        """
        org = await _org(async_session, "realtime-save")
        await async_session.commit()
        row = next(
            r
            for r in await bundle_service.list_bundles(async_session, enabled_only=True)
            if r.architecture == bundle_service.REALTIME
        )

        await agent_options.save_bundle_selection(
            async_session,
            organization_id=org.id,
            bundle_slug=row.slug,
            tier=row.realtime_tier,
            voice="",
        )

        stored = await get_organization_ai_model_configuration_v2(org.id)
        effective = compile_ai_model_configuration_v2(stored)
        assert effective.is_realtime is True
        assert effective.realtime is not None
        assert effective.realtime.model == row.realtime_tier
        # No transcriber and no separate voice: emitting both would describe an
        # agent that cannot exist, and the compiler would have to pick one.
        assert effective.stt is None
        assert effective.tts is None


class TestWhatTheScreenMayNotSave:
    async def test_a_bundle_that_is_not_on_offer_is_refused(
        self, db_session, async_session, seeded
    ):
        org = await _org(async_session, "unknown-bundle")
        await async_session.commit()

        with pytest.raises(agent_options.SelectionError):
            await agent_options.save_bundle_selection(
                async_session,
                organization_id=org.id,
                bundle_slug="does-not-exist",
                tier="default",
                voice="",
            )

    async def test_a_brain_the_bundle_does_not_offer_is_refused(
        self, db_session, async_session, seeded
    ):
        """Not merely wrong — unpriceable.

        The card quoted a price for one of three tiers. Storing a fourth name
        would run the account on whatever ``resolve`` falls back to, at a price
        nobody was shown.
        """
        org = await _org(async_session, "unknown-brain")
        await async_session.commit()

        with pytest.raises(agent_options.SelectionError):
            await agent_options.save_bundle_selection(
                async_session,
                organization_id=org.id,
                bundle_slug="everyday",
                tier="genius",
                voice="",
            )

    async def test_a_voice_we_do_not_offer_is_refused(
        self, db_session, async_session, seeded
    ):
        org = await _org(async_session, "unknown-voice")
        await async_session.commit()

        with pytest.raises(agent_options.SelectionError):
            await agent_options.save_bundle_selection(
                async_session,
                organization_id=org.id,
                bundle_slug="everyday",
                tier="default",
                voice="sir-david-attenborough",
            )


class TestReadingBackWhatIsInForce:
    async def test_an_account_that_never_saved_has_no_selection(
        self, db_session, async_session, seeded
    ):
        """``None``, not the first card.

        The picker opens on its default either way; the difference is whether
        it claims that default is saved. Saying so on an account that never
        pressed Save is how somebody leaves a screen believing their agents
        moved when they did not.
        """
        org = await _org(async_session, "never-saved")
        await async_session.commit()

        assert await agent_options.selected_bundle(organization_id=org.id) is None

    async def test_what_was_saved_is_what_comes_back(
        self, db_session, async_session, seeded
    ):
        org = await _org(async_session, "round-trip")
        await async_session.commit()
        voice = agent_options.voices()[1].voice_id

        written = await agent_options.save_bundle_selection(
            async_session,
            organization_id=org.id,
            bundle_slug="everyday",
            tier="lite",
            voice=voice,
        )

        assert written == {"bundle": "everyday", "tier": "lite", "voice": voice}
        assert await agent_options.selected_bundle(organization_id=org.id) == written

    async def test_a_realtime_selection_reports_its_realtime_tier(
        self, db_session, async_session, seeded
    ):
        """Not the llm_tier carried beside it.

        A realtime configuration stores both — the llm slot names the same
        model — and reporting the wrong one would open the picker on a brain
        the bundle does not even offer.
        """
        org = await _org(async_session, "realtime-readback")
        await async_session.commit()
        row = next(
            r
            for r in await bundle_service.list_bundles(async_session, enabled_only=True)
            if r.architecture == bundle_service.REALTIME
        )

        await agent_options.save_bundle_selection(
            async_session,
            organization_id=org.id,
            bundle_slug=row.slug,
            tier=row.realtime_tier,
            voice="",
        )

        selection = await agent_options.selected_bundle(organization_id=org.id)
        assert selection["bundle"] == row.slug
        assert selection["tier"] == row.realtime_tier
