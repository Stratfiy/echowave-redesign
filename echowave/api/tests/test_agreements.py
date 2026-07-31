"""Recording what a customer accepted.

A click-wrap is only worth what can be shown about it. These tests are about the
two ways the record fails to be evidence: it does not say *which version* was
accepted, or the client got to decide what it was accepting.
"""

import pytest

from api.db.models import AgreementAcceptanceModel, OrganizationModel, UserModel
from api.services.compliance import agreements


async def _account(session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    return org, user


@pytest.mark.asyncio
class TestRecordingAnAcceptance:
    async def test_it_stores_the_published_version_not_a_boolean(self, async_session):
        """ "They accepted the DPA" is not a defence once the DPA has been
        revised. The version is the whole point of the record."""
        org, user = await _account(async_session, "v")

        row = await agreements.record_acceptance(
            async_session,
            organization_id=org.id,
            user_id=user.id,
            agreement="dpa",
        )

        assert row.version == agreements.CURRENT_VERSIONS["dpa"]

    async def test_the_evidential_fields_are_kept(self, async_session):
        org, user = await _account(async_session, "e")

        row = await agreements.record_acceptance(
            async_session,
            organization_id=org.id,
            user_id=user.id,
            agreement="dpa",
            ip_address="203.0.113.7",
            user_agent="Mozilla/5.0",
        )

        assert row.ip_address == "203.0.113.7"
        assert row.user_agent == "Mozilla/5.0"
        assert row.accepted_at is not None

    async def test_an_unknown_agreement_is_refused(self, async_session):
        """Otherwise a client could record acceptance of a document nobody
        published, and the row would look identical to a real one."""
        org, user = await _account(async_session, "u")

        with pytest.raises(ValueError, match="Unknown agreement"):
            await agreements.record_acceptance(
                async_session,
                organization_id=org.id,
                user_id=user.id,
                agreement="something-invented",
            )

    async def test_an_overlong_user_agent_is_truncated_not_rejected(
        self, async_session
    ):
        """A browser sending a 2KB UA string must not cost the customer their
        acceptance record."""
        org, user = await _account(async_session, "t")

        row = await agreements.record_acceptance(
            async_session,
            organization_id=org.id,
            user_id=user.id,
            agreement="dpa",
            user_agent="x" * 5000,
        )

        assert len(row.user_agent) == 512

    async def test_superseding_writes_a_new_row(self, async_session):
        """Never an update. In a dispute the question is what was agreed at the
        time, which an editable record cannot answer."""
        org, user = await _account(async_session, "s")

        for _ in range(2):
            await agreements.record_acceptance(
                async_session,
                organization_id=org.id,
                user_id=user.id,
                agreement="dpa",
            )

        rows = (
            await async_session.execute(
                AgreementAcceptanceModel.__table__.select().where(
                    AgreementAcceptanceModel.organization_id == org.id
                )
            )
        ).all()
        assert len(rows) == 2


@pytest.mark.asyncio
class TestWhatIsStillOutstanding:
    async def test_a_new_account_owes_every_required_agreement(self, async_session):
        org, _ = await _account(async_session, "new")

        outstanding = await agreements.outstanding_for(
            async_session, organization_id=org.id
        )

        required = [a.key for a in agreements.AGREEMENTS if a.required]
        assert {a.key for a in outstanding} == set(required)

    async def test_accepting_clears_it(self, async_session):
        org, user = await _account(async_session, "clear")

        for key in [a.key for a in agreements.AGREEMENTS if a.required]:
            await agreements.record_acceptance(
                async_session,
                organization_id=org.id,
                user_id=user.id,
                agreement=key,
            )

        assert (
            await agreements.outstanding_for(async_session, organization_id=org.id)
            == ()
        )

    async def test_a_stale_version_is_still_outstanding(self, async_session):
        """The behaviour that makes versioning worth having: an account that
        accepted last year's terms has not accepted this year's, and reads the
        same as one that never accepted at all."""
        org, user = await _account(async_session, "stale")

        async_session.add(
            AgreementAcceptanceModel(
                organization_id=org.id,
                user_id=user.id,
                agreement="dpa",
                version="1999-01",
            )
        )
        await async_session.flush()

        outstanding = await agreements.outstanding_for(
            async_session, organization_id=org.id
        )

        assert "dpa" in {a.key for a in outstanding}

    async def test_another_account_is_never_counted(self, async_session):
        theirs, their_user = await _account(async_session, "theirs")
        mine, _ = await _account(async_session, "mine")

        for key in [a.key for a in agreements.AGREEMENTS if a.required]:
            await agreements.record_acceptance(
                async_session,
                organization_id=theirs.id,
                user_id=their_user.id,
                agreement=key,
            )

        outstanding = await agreements.outstanding_for(
            async_session, organization_id=mine.id
        )
        assert outstanding


class TestThePublishedSet:
    def test_the_dpa_is_required(self):
        """Without it the consent obligation for the people being called has
        not been allocated to anyone, which is the liability the whole
        Processor/Fiduciary split exists to avoid."""
        dpa = next(a for a in agreements.AGREEMENTS if a.key == "dpa")

        assert dpa.required

    def test_every_agreement_has_a_version_and_a_url(self):
        for agreement in agreements.AGREEMENTS:
            assert agreement.version
            assert agreement.url.startswith("https://")
