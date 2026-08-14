"""Staff setting an account's ceilings, and the four ways that goes wrong.

The enforcement is tested in ``test_account_limits.py``. This is the other
half — the screen staff actually use — and every test here is about a way a
correct enforcer can still be handed the wrong number.

**Effective is not configured.** A screen showing one figure cannot tell "5,
because that is the default" from "5, because somebody chose 5", and the
difference decides whether raising the platform default moves this account.

**Clearing has to be possible.** Otherwise the only way to undo an override is
to retype today's default, which freezes the account at that number the next
time the default moves — an override nobody knows they created.

**Zero means opposite things.** On the spend ceiling it means no ceiling; on
concurrency it would mean no call may ever start. One of those is a support
ticket that takes a day to find.

**A typo is a carrier bill.** A maximum is not a commercial judgement, it is a
fat-finger guard.
"""

import pytest

from api.constants import (
    DEFAULT_DAILY_SPEND_CEILING_PAISE,
    DEFAULT_INBOUND_CONCURRENCY,
    DEFAULT_OUTBOUND_CONCURRENCY,
)
from api.db.models import OrganizationConfigurationModel, OrganizationModel
from api.enums import OrganizationConfigurationKey
from api.services import account_limits


async def _org(async_session, slug):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


@pytest.mark.asyncio
class TestReading:
    async def test_an_untouched_account_reads_as_the_defaults(
        self, db_session, async_session
    ):
        org = await _org(async_session, "lim-fresh")

        limits = await account_limits.read(async_session, organization_id=org.id)

        assert limits["inbound_concurrency"]["effective"] == DEFAULT_INBOUND_CONCURRENCY
        assert (
            limits["outbound_concurrency"]["effective"] == DEFAULT_OUTBOUND_CONCURRENCY
        )
        assert (
            limits["daily_spend_ceiling_paise"]["effective"]
            == DEFAULT_DAILY_SPEND_CEILING_PAISE
        )

    async def test_a_default_is_reported_as_unconfigured(
        self, db_session, async_session
    ):
        """The distinction the screen is for. Without it, staff cannot tell a
        number somebody chose from a number nobody has touched — and only the
        second one moves when the platform default does."""
        org = await _org(async_session, "lim-unset")

        limits = await account_limits.read(async_session, organization_id=org.id)

        assert limits["inbound_concurrency"]["configured"] is None

    async def test_an_override_is_reported_as_configured(
        self, db_session, async_session
    ):
        org = await _org(async_session, "lim-set")
        await account_limits.set_limit(
            async_session,
            organization_id=org.id,
            field="inbound_concurrency",
            value=40,
        )
        await async_session.flush()

        limits = await account_limits.read(async_session, organization_id=org.id)

        assert limits["inbound_concurrency"]["configured"] == 40
        assert limits["inbound_concurrency"]["effective"] == 40
        assert limits["inbound_concurrency"]["default"] == DEFAULT_INBOUND_CONCURRENCY

    async def test_the_older_blind_limit_is_shown_not_hidden(
        self, db_session, async_session
    ):
        """An account carrying the direction-blind limit is bound by it in both
        directions. A screen that hid it would explain neither the number being
        enforced nor how to change it — which is how somebody 'raises' a limit
        and watches nothing happen."""
        org = await _org(async_session, "lim-legacy")
        async_session.add(
            OrganizationConfigurationModel(
                organization_id=org.id,
                key=OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value,
                value={"value": 3},
            )
        )
        await async_session.flush()

        limits = await account_limits.read(async_session, organization_id=org.id)

        assert limits["legacy_concurrency"] == 3
        # And the effective figures match what the slot allocator will do.
        assert limits["inbound_concurrency"]["effective"] == 3
        assert limits["outbound_concurrency"]["effective"] == 3

    async def test_a_direction_override_beats_the_blind_limit_on_screen_too(
        self, db_session, async_session
    ):
        """The screen has to agree with the enforcer, or it is a second source
        of truth that is wrong half the time."""
        org = await _org(async_session, "lim-both")
        async_session.add(
            OrganizationConfigurationModel(
                organization_id=org.id,
                key=OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value,
                value={"value": 3},
            )
        )
        await account_limits.set_limit(
            async_session,
            organization_id=org.id,
            field="outbound_concurrency",
            value=25,
        )
        await async_session.flush()

        limits = await account_limits.read(async_session, organization_id=org.id)

        assert limits["outbound_concurrency"]["effective"] == 25
        assert limits["inbound_concurrency"]["effective"] == 3

    async def test_one_accounts_limits_are_not_anothers(
        self, db_session, async_session
    ):
        mine = await _org(async_session, "lim-mine")
        theirs = await _org(async_session, "lim-theirs")
        await account_limits.set_limit(
            async_session,
            organization_id=theirs.id,
            field="inbound_concurrency",
            value=99,
        )
        await async_session.flush()

        limits = await account_limits.read(async_session, organization_id=mine.id)

        assert limits["inbound_concurrency"]["configured"] is None


@pytest.mark.asyncio
class TestWriting:
    async def test_setting_then_clearing_returns_to_the_default(
        self, db_session, async_session
    ):
        """Clearing has to work without knowing what the default is, or the
        only way to undo an override is to retype today's default — which
        silently pins the account there when the default next moves."""
        org = await _org(async_session, "lim-clear")
        await account_limits.set_limit(
            async_session, organization_id=org.id, field="inbound_concurrency", value=40
        )
        await async_session.flush()
        await account_limits.set_limit(
            async_session,
            organization_id=org.id,
            field="inbound_concurrency",
            value=None,
        )
        await async_session.flush()

        limits = await account_limits.read(async_session, organization_id=org.id)

        assert limits["inbound_concurrency"]["configured"] is None
        assert limits["inbound_concurrency"]["effective"] == DEFAULT_INBOUND_CONCURRENCY

    async def test_setting_twice_updates_rather_than_duplicating(
        self, db_session, async_session
    ):
        """One row per key. Two rows and which one wins is whichever the query
        happens to return first."""
        import sqlalchemy as sa

        org = await _org(async_session, "lim-twice")
        for value in (10, 20):
            await account_limits.set_limit(
                async_session,
                organization_id=org.id,
                field="outbound_concurrency",
                value=value,
            )
            await async_session.flush()

        rows = (
            (
                await async_session.execute(
                    sa.select(OrganizationConfigurationModel).where(
                        OrganizationConfigurationModel.organization_id == org.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].value == {"value": 20}

    async def test_a_spend_ceiling_of_zero_is_allowed(self, db_session, async_session):
        """The escape hatch. An enterprise account on an invoice should not be
        stopped by a breaker meant for card fraud."""
        org = await _org(async_session, "lim-zero-spend")
        await account_limits.set_limit(
            async_session,
            organization_id=org.id,
            field="daily_spend_ceiling_paise",
            value=0,
        )
        await async_session.flush()

        limits = await account_limits.read(async_session, organization_id=org.id)

        assert limits["daily_spend_ceiling_paise"]["effective"] == 0

    async def test_a_concurrency_of_zero_is_refused(self, db_session, async_session):
        """Zero means 'no ceiling' one field up and 'no call may ever start'
        here. Applying either meaning silently is a support ticket that takes a
        day to find; refusing takes a second."""
        org = await _org(async_session, "lim-zero-conc")

        with pytest.raises(account_limits.LimitError):
            await account_limits.set_limit(
                async_session,
                organization_id=org.id,
                field="inbound_concurrency",
                value=0,
            )

    async def test_an_absurd_concurrency_is_refused(self, db_session, async_session):
        """Not a commercial judgement — a fat-finger guard. The failure a typo
        produces here is a carrier bill rather than an error message."""
        org = await _org(async_session, "lim-huge")

        with pytest.raises(account_limits.LimitError):
            await account_limits.set_limit(
                async_session,
                organization_id=org.id,
                field="outbound_concurrency",
                value=account_limits.MAX_CONCURRENCY + 1,
            )

    async def test_an_absurd_spend_ceiling_is_refused(self, db_session, async_session):
        org = await _org(async_session, "lim-huge-spend")

        with pytest.raises(account_limits.LimitError):
            await account_limits.set_limit(
                async_session,
                organization_id=org.id,
                field="daily_spend_ceiling_paise",
                value=account_limits.MAX_DAILY_SPEND_PAISE + 1,
            )

    async def test_an_unknown_field_is_refused(self, db_session, async_session):
        """A typo in a field name that silently did nothing would look exactly
        like a saved setting that is not being enforced."""
        org = await _org(async_session, "lim-unknown")

        with pytest.raises(account_limits.LimitError):
            await account_limits.set_limit(
                async_session,
                organization_id=org.id,
                field="inbound_concurency",
                value=5,
            )


@pytest.mark.asyncio
class TestWhatWasSetIsWhatIsEnforced:
    """The wiring, not the unit.

    A settings screen that writes somewhere the enforcer does not read is
    indistinguishable from no feature at all, and it fails in the most
    expensive direction — staff believe a limit is in place.
    """

    async def test_the_slot_allocator_reads_what_staff_set(
        self, db_session, async_session
    ):
        from api.services.call_concurrency import CallConcurrencyService

        org = await _org(async_session, "lim-wired")
        await account_limits.set_limit(
            async_session,
            organization_id=org.id,
            field="outbound_concurrency",
            value=17,
        )
        await async_session.commit()

        service = CallConcurrencyService()
        assert (
            await service.get_org_concurrent_limit(org.id, direction="outbound") == 17
        )

    async def test_the_spend_breaker_reads_what_staff_set(
        self, db_session, async_session
    ):
        from api.services.billing import spend_ceiling

        org = await _org(async_session, "lim-wired-spend")
        await account_limits.set_limit(
            async_session,
            organization_id=org.id,
            field="daily_spend_ceiling_paise",
            value=250_000,
        )
        await async_session.commit()

        assert (
            await spend_ceiling.ceiling_for(async_session, organization_id=org.id)
            == 250_000
        )
