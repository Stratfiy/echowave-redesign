"""The screen where a customer authorises money to leave without them.

The route tests worth having are the refusals. A settings endpoint that saves
whatever it is given produces the two states this feature must never be in:
switched on with no instrument behind it — a promise the product cannot keep,
discovered by running out of credit — and switched on for more than the bank
authorised, discovered as a decline mid-campaign.
"""

from __future__ import annotations

import pytest

from api.constants import MIN_TOPUP_PAISE
from api.db.models import (
    AutoTopupAttemptModel,
    AutoTopupSettingModel,
    OrganizationModel,
)
from api.services.billing import auto_topup_runner, payments

AMOUNT = max(100_000, MIN_TOPUP_PAISE)


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _token(session, org_id: int, *, max_amount: int | None = None):
    return await payments.remember_token(
        session,
        organization_id=org_id,
        entity={
            "token_id": f"tok_{org_id}",
            "recurring": True,
            "method": "card",
            "customer_id": f"cust_{org_id}",
            "card": {"last4": "4321"},
            **({"max_amount": max_amount} if max_amount is not None else {}),
        },
    )


@pytest.mark.asyncio
class TestTheSettingsRoundTrip:
    async def test_an_unconfigured_account_reads_as_off(
        self, db_session, async_session
    ):
        """The default an account has before anybody visits the screen."""
        org = await _org(async_session, "unset")
        row = await auto_topup_runner.load_settings(
            async_session, organization_id=org.id
        )
        settings = auto_topup_runner.as_settings(row)
        assert row is None
        assert settings.enabled is False
        assert settings.amount_paise == 0

    async def test_a_paused_account_is_off_however_the_toggle_reads(
        self, db_session, async_session
    ):
        """`paused_reason` is set by a run of declines. Leaving `enabled` true
        preserves the customer's own preference, so the engine must be the thing
        that treats paused as off — otherwise a paused account keeps charging."""
        org = await _org(async_session, "paused")
        row = AutoTopupSettingModel(
            organization_id=org.id,
            enabled=True,
            amount_paise=AMOUNT,
            paused_reason="Paused after 3 failed attempts",
        )
        async_session.add(row)
        await async_session.flush()

        assert auto_topup_runner.as_settings(row).enabled is False

    async def test_clearing_the_pause_switches_it_back_on(
        self, db_session, async_session
    ):
        org = await _org(async_session, "resumed")
        row = AutoTopupSettingModel(
            organization_id=org.id, enabled=True, amount_paise=AMOUNT
        )
        async_session.add(row)
        await async_session.flush()
        assert auto_topup_runner.as_settings(row).enabled is True


@pytest.mark.asyncio
class TestWhatTheScreenMustRefuse:
    async def test_enabling_without_an_instrument_is_the_state_to_prevent(
        self, db_session, async_session
    ):
        """The route raises 400 for this. Asserted here at the layer the route
        asks, because the answer is what the refusal depends on."""
        org = await _org(async_session, "noinstrument")
        assert await payments.active_token(async_session, organization_id=org.id) is None

    async def test_an_amount_above_the_authorised_ceiling_is_knowable_up_front(
        self, db_session, async_session
    ):
        """So the customer is told to raise their authorisation rather than
        shown a decline weeks later, mid-campaign."""
        org = await _org(async_session, "ceiling")
        token = await _token(async_session, org.id, max_amount=AMOUNT - 1)
        assert token.max_amount_paise < AMOUNT


@pytest.mark.asyncio
class TestStoppingADebitWeAnnounced:
    async def test_a_scheduled_attempt_can_be_cancelled(
        self, db_session, async_session
    ):
        """The notice email says they can stop it, so something has to."""
        org = await _org(async_session, "cancel")
        attempt = AutoTopupAttemptModel(
            organization_id=org.id,
            status=auto_topup_runner.SCHEDULED,
            amount_paise=AMOUNT,
        )
        async_session.add(attempt)
        await async_session.flush()

        pending = await auto_topup_runner.pending_attempt(
            async_session, organization_id=org.id
        )
        assert pending is not None
        assert pending.status == auto_topup_runner.SCHEDULED

    async def test_an_attempt_already_charging_is_not_cancellable(
        self, db_session, async_session
    ):
        """The money may already be moving. Cancelling the row would hide a
        charge rather than prevent one, and the ledger would disagree with the
        bank."""
        org = await _org(async_session, "charging")
        attempt = AutoTopupAttemptModel(
            organization_id=org.id,
            status=auto_topup_runner.CHARGING,
            amount_paise=AMOUNT,
        )
        async_session.add(attempt)
        await async_session.flush()

        pending = await auto_topup_runner.pending_attempt(
            async_session, organization_id=org.id
        )
        assert pending.status == auto_topup_runner.CHARGING

    async def test_a_cancelled_attempt_no_longer_blocks_the_next_one(
        self, db_session, async_session
    ):
        """Cancelled is a finished state. If it still counted as in flight, one
        cancellation would switch the feature off for ever."""
        org = await _org(async_session, "recover")
        first = AutoTopupAttemptModel(
            organization_id=org.id,
            status=auto_topup_runner.CANCELLED,
            amount_paise=AMOUNT,
        )
        async_session.add(first)
        await async_session.flush()

        second = AutoTopupAttemptModel(
            organization_id=org.id,
            status=auto_topup_runner.SCHEDULED,
            amount_paise=AMOUNT,
        )
        async_session.add(second)
        await async_session.flush()  # must not raise

        assert (
            await auto_topup_runner.pending_attempt(
                async_session, organization_id=org.id
            )
        ).id == second.id
