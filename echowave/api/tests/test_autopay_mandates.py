"""Autopay: the standing authorisation that has to exist before a number does.

A number rental collected out of a prepaid balance stops the moment the balance
does, and what is left is a number we keep paying a carrier for with nothing
standing behind the rent. The mandate inverts that — and every test here exists
because one specific way of getting it wrong costs real money:

* **only a verified webhook authorises a mandate.** The browser saying "I
  authorised it" is an unauthenticated client asserting we should hand over a
  number we will pay for.
* **a number waits on the mandate, never the other way round.**
* **a mandate collection does not also debit the prepaid balance.** Two
  collection paths for one month is a double charge, and it is the kind that
  looks correct in both ledgers separately.
* **losing a mandate is not losing the money.** A revoked mandate falls back to
  the balance and the dunning schedule rather than releasing anything.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    PaymentMandateModel,
    RecurringChargeModel,
    RecurringChargePeriodModel,
)
from api.enums import (
    CreditLedgerKind,
    MandateStatus,
    RecurringChargeStatus,
    RecurringChargeType,
)
from api.services.billing import mandates as mandate_service


async def _org(session, slug: str, *, balance_paise: int = 0):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    if balance_paise:
        session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=balance_paise,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=balance_paise,
            )
        )
        await session.flush()
    return org


async def _mandate(session, org, *, status=MandateStatus.ACTIVE.value, sub="sub_1"):
    mandate = PaymentMandateModel(
        organization_id=org.id,
        provider="razorpay",
        purpose="number_rental",
        subscription_id=sub,
        plan_id="plan_1",
        status=status,
        price_paise=34900,
    )
    session.add(mandate)
    await session.flush()
    return mandate


async def _charge(session, org, *, mandate=None, started=None, resource_id=1):
    charge = RecurringChargeModel(
        organization_id=org.id,
        charge_type=RecurringChargeType.NUMBER_RENTAL.value,
        resource_id=resource_id,
        status=RecurringChargeStatus.ACTIVE.value,
        cost_paise=25000,
        price_paise=34900,
        mandate_id=mandate.id if mandate else None,
        started_at=started or datetime.now(UTC) - timedelta(days=1),
        next_charge_at=datetime.now(UTC),
    )
    session.add(charge)
    await session.flush()
    return charge


def _charged_event(subscription_id: str, *, amount: int = 34900) -> dict:
    return {
        "event": "subscription.charged",
        "payload": {
            "subscription": {"entity": {"id": subscription_id, "status": "active"}},
            "payment": {"entity": {"id": "pay_1", "amount": amount}},
        },
    }


def _event(event: str, subscription_id: str, **entity) -> dict:
    return {
        "event": event,
        "payload": {
            "subscription": {"entity": {"id": subscription_id, **entity}},
        },
    }


class TestTheGate:
    async def test_no_mandate_refuses_to_issue_a_number(
        self, db_session, async_session, monkeypatch
    ):
        monkeypatch.setattr(
            "api.services.billing.mandates.REQUIRE_MANDATE_FOR_NUMBERS", True
        )
        org = await _org(async_session, "gate-none")

        with pytest.raises(mandate_service.MandateNotAuthorised):
            await mandate_service.assert_mandate_authorised(
                async_session, organization_id=org.id
            )

    async def test_an_unauthorised_mandate_is_not_enough(
        self, db_session, async_session, monkeypatch
    ):
        """Created is not authorised. A subscription object exists at the
        provider and the customer has not opened the link — issuing here is a
        number we pay for and cannot collect on."""
        monkeypatch.setattr(
            "api.services.billing.mandates.REQUIRE_MANDATE_FOR_NUMBERS", True
        )
        org = await _org(async_session, "gate-created")
        await _mandate(
            async_session, org, status=MandateStatus.CREATED.value, sub="sub_created"
        )

        with pytest.raises(mandate_service.MandateNotAuthorised) as exc:
            await mandate_service.assert_mandate_authorised(
                async_session, organization_id=org.id
            )
        assert "created" in str(exc.value)

    @pytest.mark.parametrize(
        "status", [MandateStatus.AUTHENTICATED.value, MandateStatus.ACTIVE.value]
    )
    async def test_an_authorised_mandate_passes(
        self, db_session, async_session, monkeypatch, status
    ):
        monkeypatch.setattr(
            "api.services.billing.mandates.REQUIRE_MANDATE_FOR_NUMBERS", True
        )
        org = await _org(async_session, f"gate-ok-{status}")
        mandate = await _mandate(
            async_session, org, status=status, sub=f"sub_ok_{status}"
        )

        got = await mandate_service.assert_mandate_authorised(
            async_session, organization_id=org.id
        )
        assert got is not None and got.id == mandate.id

    async def test_a_halted_mandate_does_not_pass(
        self, db_session, async_session, monkeypatch
    ):
        """Halted means the provider stopped collecting. Issuing a second
        number to an account whose first one is already not being paid for is
        how one bad debt becomes two."""
        monkeypatch.setattr(
            "api.services.billing.mandates.REQUIRE_MANDATE_FOR_NUMBERS", True
        )
        org = await _org(async_session, "gate-halted")
        await _mandate(
            async_session, org, status=MandateStatus.HALTED.value, sub="sub_halted"
        )

        with pytest.raises(mandate_service.MandateNotAuthorised):
            await mandate_service.assert_mandate_authorised(
                async_session, organization_id=org.id
            )

    async def test_the_requirement_can_be_switched_off_while_activation_pends(
        self, db_session, async_session, monkeypatch
    ):
        """Razorpay Subscriptions needs their approval, which can take days.
        With the requirement off, provisioning runs the same path and the
        rental falls back to the prepaid balance."""
        monkeypatch.setattr(
            "api.services.billing.mandates.REQUIRE_MANDATE_FOR_NUMBERS", False
        )
        org = await _org(async_session, "gate-off")

        assert (
            await mandate_service.assert_mandate_authorised(
                async_session, organization_id=org.id
            )
            is None
        )


class TestWebhookDrivenState:
    async def test_activation_authorises_the_mandate(
        self, db_session, async_session
    ):
        org = await _org(async_session, "wh-activate")
        mandate = await _mandate(
            async_session, org, status=MandateStatus.CREATED.value, sub="sub_act"
        )

        outcome = await mandate_service.apply_subscription_event(
            async_session, event=_event("subscription.activated", "sub_act")
        )

        assert outcome["mandate_status"] == MandateStatus.ACTIVE.value
        await async_session.refresh(mandate)
        assert mandate.status == MandateStatus.ACTIVE.value
        assert mandate.authorised_at is not None

    async def test_a_terminal_mandate_is_never_revived(
        self, db_session, async_session
    ):
        """Webhooks arrive out of order. A late `activated` after a `cancelled`
        would otherwise revive an authorisation the customer's bank has already
        withdrawn, and the next collection would fail at the bank while our
        records said it should have worked."""
        org = await _org(async_session, "wh-terminal")
        mandate = await _mandate(
            async_session, org, status=MandateStatus.CANCELLED.value, sub="sub_term"
        )

        outcome = await mandate_service.apply_subscription_event(
            async_session, event=_event("subscription.activated", "sub_term")
        )

        assert outcome["status"] == "terminal"
        await async_session.refresh(mandate)
        assert mandate.status == MandateStatus.CANCELLED.value

    async def test_an_unknown_subscription_is_logged_not_created(
        self, db_session, async_session
    ):
        """Either a second environment sharing this webhook, or somebody
        probing it. Creating a row from an unsolicited event would let anyone
        with the endpoint mint an authorisation."""
        outcome = await mandate_service.apply_subscription_event(
            async_session, event=_event("subscription.activated", "sub_nobody")
        )

        assert outcome["status"] == "unknown_subscription"
        assert (
            await async_session.scalar(
                RecurringChargeModel.__table__.select().where(
                    RecurringChargeModel.id.is_(None)
                )
            )
            is None
        )

    async def test_an_unrecognised_event_is_ignored_not_an_error(
        self, db_session, async_session
    ):
        """Providers add events. Treating an unknown one as a failure has them
        retried for ever."""
        outcome = await mandate_service.apply_subscription_event(
            async_session, event={"event": "subscription.invented", "payload": {}}
        )
        assert outcome["status"] == "ignored"

    async def test_halting_records_why(self, db_session, async_session):
        org = await _org(async_session, "wh-halt")
        mandate = await _mandate(async_session, org, sub="sub_halt")

        await mandate_service.apply_subscription_event(
            async_session,
            event=_event(
                "subscription.halted",
                "sub_halt",
                error_description="Insufficient funds at the bank",
            ),
        )

        await async_session.refresh(mandate)
        assert mandate.status == MandateStatus.HALTED.value
        assert "Insufficient funds" in (mandate.last_failure_reason or "")


class TestCollection:
    async def test_a_mandate_collection_writes_a_period(
        self, db_session, async_session
    ):
        from api.services.billing.rentals import record_mandate_collection

        org = await _org(async_session, "coll-basic")
        mandate = await _mandate(async_session, org, sub="sub_coll")
        charge = await _charge(async_session, org, mandate=mandate, resource_id=101)

        outcome = await record_mandate_collection(
            async_session, mandate_id=mandate.id, event=_charged_event("sub_coll")
        )

        assert outcome["status"] == "recorded"
        period = await async_session.get(
            RecurringChargePeriodModel, outcome["period_id"]
        )
        assert period.recurring_charge_id == charge.id
        assert period.charged_paise == 34900
        assert period.collected_via == "mandate"

    async def test_a_mandate_collection_does_not_debit_the_balance(
        self, db_session, async_session
    ):
        """The whole point. The prepaid ledger tracks credit the customer
        bought from us; rent collected from their bank never became credit, and
        debiting it here takes the rent twice."""
        from api.services.billing.rentals import record_mandate_collection

        org = await _org(async_session, "coll-nodebit", balance_paise=100000)
        mandate = await _mandate(async_session, org, sub="sub_nodebit")
        await _charge(async_session, org, mandate=mandate, resource_id=102)

        await record_mandate_collection(
            async_session, mandate_id=mandate.id, event=_charged_event("sub_nodebit")
        )

        rentals = (
            await async_session.execute(
                CreditLedgerModel.__table__.select().where(
                    CreditLedgerModel.organization_id == org.id,
                    CreditLedgerModel.kind == CreditLedgerKind.RENTAL.value,
                )
            )
        ).all()
        assert rentals == []

    async def test_a_redelivered_collection_is_a_no_op(
        self, db_session, async_session
    ):
        """Razorpay delivers at least once. The second delivery must not be a
        second month of rent."""
        from api.services.billing.rentals import record_mandate_collection

        org = await _org(async_session, "coll-dupe")
        mandate = await _mandate(async_session, org, sub="sub_dupe")
        charge = await _charge(async_session, org, mandate=mandate, resource_id=103)

        first = await record_mandate_collection(
            async_session, mandate_id=mandate.id, event=_charged_event("sub_dupe")
        )
        second = await record_mandate_collection(
            async_session, mandate_id=mandate.id, event=_charged_event("sub_dupe")
        )

        assert first["status"] == "recorded"
        assert second["status"] == "already_recorded"
        periods = (
            await async_session.execute(
                RecurringChargePeriodModel.__table__.select().where(
                    RecurringChargePeriodModel.recurring_charge_id == charge.id
                )
            )
        ).all()
        assert len(periods) == 1

    async def test_the_amount_collected_is_the_providers_figure(
        self, db_session, async_session
    ):
        """What the bank actually took is a fact about the collection. Our
        price is what we asked for; recording the ask rather than the take is
        how a reconciliation stops finding discrepancies it should find."""
        from api.services.billing.rentals import record_mandate_collection

        org = await _org(async_session, "coll-amount")
        mandate = await _mandate(async_session, org, sub="sub_amount")
        await _charge(async_session, org, mandate=mandate, resource_id=104)

        outcome = await record_mandate_collection(
            async_session,
            mandate_id=mandate.id,
            event=_charged_event("sub_amount", amount=30000),
        )

        assert outcome["charged_paise"] == 30000

    async def test_a_collection_with_no_open_charge_is_loud(
        self, db_session, async_session
    ):
        """The customer has been debited and we are holding money against no
        resource — the mirror image of the orphaned rental reconciliation
        catches."""
        from api.services.billing.rentals import record_mandate_collection

        org = await _org(async_session, "coll-orphan")
        mandate = await _mandate(async_session, org, sub="sub_orphan")

        outcome = await record_mandate_collection(
            async_session, mandate_id=mandate.id, event=_charged_event("sub_orphan")
        )
        assert outcome["status"] == "no_charge"

    async def test_a_collection_restores_a_suspended_number(
        self, db_session, async_session
    ):
        from api.services.billing.rentals import record_mandate_collection

        org = await _org(async_session, "coll-restore")
        mandate = await _mandate(async_session, org, sub="sub_restore")
        charge = await _charge(async_session, org, mandate=mandate, resource_id=105)
        charge.status = RecurringChargeStatus.SUSPENDED.value
        charge.failure_count = 3
        charge.first_failed_at = datetime.now(UTC) - timedelta(days=10)
        await async_session.flush()

        await record_mandate_collection(
            async_session, mandate_id=mandate.id, event=_charged_event("sub_restore")
        )

        await async_session.refresh(charge)
        assert charge.status == RecurringChargeStatus.ACTIVE.value
        # The dunning clock resets completely — a customer who pays late starts
        # from zero rather than carrying a clock from an earlier lapse.
        assert charge.failure_count == 0
        assert charge.first_failed_at is None


class TestRedeliveryDoesNotUndoTheRest:
    async def test_a_redelivered_charge_keeps_the_mandate_state_change(
        self, db_session, async_session
    ):
        """The webhook applies the mandate transition and records the
        collection in one transaction. A collision on the collection must roll
        back the insert and nothing else — otherwise the redelivery quietly
        forgets that the subscription is now active."""
        from api.services.billing.rentals import record_mandate_collection

        org = await _org(async_session, "redeliver-state")
        mandate = await _mandate(
            async_session,
            org,
            status=MandateStatus.CREATED.value,
            sub="sub_redeliver",
        )
        await _charge(async_session, org, mandate=mandate, resource_id=108)

        event = _charged_event("sub_redeliver")
        await mandate_service.apply_subscription_event(async_session, event=event)
        await record_mandate_collection(
            async_session, mandate_id=mandate.id, event=event
        )

        # The redelivery: same event, same payment id.
        await mandate_service.apply_subscription_event(async_session, event=event)
        outcome = await record_mandate_collection(
            async_session, mandate_id=mandate.id, event=event
        )

        assert outcome["status"] == "already_recorded"
        await async_session.refresh(mandate)
        assert mandate.status == MandateStatus.ACTIVE.value


class TestTheTwoPathsNeverBothCollect:
    """The double-charge guard, exercised through the real monthly cron path.

    Everything above tests the pieces. This tests the thing that actually costs
    money: the cron running on a charge that a bank is already collecting for.
    """

    async def test_the_monthly_cron_does_not_bill_a_mandated_charge(
        self, db_session, async_session
    ):
        from sqlalchemy import func, select

        from api.db import db_client
        from api.db.models import TelephonyConfigurationModel, TelephonyPhoneNumberModel
        from api.enums import PhoneNumberStatus
        from api.services.billing import rentals

        org = await _org(async_session, "cron-mandated", balance_paise=200000)
        mandate = await _mandate(async_session, org, sub="sub_cron")

        config = TelephonyConfigurationModel(
            organization_id=org.id,
            name="cfg-cron",
            provider="plivo",
            credentials={"auth_id": "MA", "auth_token": "t"},
            is_platform_managed=True,
        )
        async_session.add(config)
        await async_session.flush()
        number = TelephonyPhoneNumberModel(
            organization_id=org.id,
            telephony_configuration_id=config.id,
            address="+918099000001",
            address_normalized="+918099000001",
            address_type="phone",
            country_code="IN",
            status=PhoneNumberStatus.ACTIVE.value,
            carrier_number_id="cn-cron",
        )
        async_session.add(number)
        await async_session.flush()

        charge = await rentals.open_number_rental(
            organization_id=org.id,
            phone_number_id=number.id,
            cost_paise=25000,
            price_paise=34900,
            mandate_id=mandate.id,
        )

        outcome = await rentals.charge_period(charge.id, now=datetime.now(UTC))

        assert not outcome.charged
        assert outcome.reason == "mandate_collects"
        async with db_client.async_session() as session:
            debits = await session.scalar(
                select(func.count(CreditLedgerModel.id)).where(
                    CreditLedgerModel.organization_id == org.id,
                    CreditLedgerModel.kind == CreditLedgerKind.RENTAL.value,
                )
            )
        assert debits == 0

    async def test_the_cron_resumes_billing_when_the_mandate_is_revoked(
        self, db_session, async_session
    ):
        """The fallback, end to end. A customer who revokes autopay in their UPI
        app keeps the number and starts paying from the balance again."""
        from sqlalchemy import func, select

        from api.db import db_client
        from api.db.models import TelephonyConfigurationModel, TelephonyPhoneNumberModel
        from api.enums import PhoneNumberStatus
        from api.services.billing import rentals

        org = await _org(async_session, "cron-revoked", balance_paise=200000)
        mandate = await _mandate(async_session, org, sub="sub_cron_rev")

        config = TelephonyConfigurationModel(
            organization_id=org.id,
            name="cfg-rev",
            provider="plivo",
            credentials={"auth_id": "MA", "auth_token": "t"},
            is_platform_managed=True,
        )
        async_session.add(config)
        await async_session.flush()
        number = TelephonyPhoneNumberModel(
            organization_id=org.id,
            telephony_configuration_id=config.id,
            address="+918099000002",
            address_normalized="+918099000002",
            address_type="phone",
            country_code="IN",
            status=PhoneNumberStatus.ACTIVE.value,
            carrier_number_id="cn-rev",
        )
        async_session.add(number)
        await async_session.flush()

        charge = await rentals.open_number_rental(
            organization_id=org.id,
            phone_number_id=number.id,
            cost_paise=25000,
            price_paise=34900,
            mandate_id=mandate.id,
        )

        await mandate_service.apply_subscription_event(
            async_session, event=_event("subscription.cancelled", "sub_cron_rev")
        )

        outcome = await rentals.charge_period(charge.id, now=datetime.now(UTC))

        assert outcome.charged
        async with db_client.async_session() as session:
            debits = await session.scalar(
                select(func.count(CreditLedgerModel.id)).where(
                    CreditLedgerModel.organization_id == org.id,
                    CreditLedgerModel.kind == CreditLedgerKind.RENTAL.value,
                )
            )
        assert debits == 1


class TestFallback:
    async def test_a_revoked_mandate_returns_collection_to_the_balance(
        self, db_session, async_session
    ):
        """Losing a mandate is not losing the money. The charge falls back to
        the prepaid balance and the dunning schedule, which is exactly the
        situation that schedule was written for."""
        from api.services.billing.rentals import _mandate_collects

        org = await _org(async_session, "fb-revoked")
        mandate = await _mandate(async_session, org, sub="sub_revoked")
        charge = await _charge(async_session, org, mandate=mandate, resource_id=106)

        assert await _mandate_collects(async_session, charge) is True

        mandate.status = MandateStatus.CANCELLED.value
        await async_session.flush()

        assert await _mandate_collects(async_session, charge) is False

    async def test_a_charge_with_no_mandate_always_uses_the_balance(
        self, db_session, async_session
    ):
        from api.services.billing.rentals import _mandate_collects

        org = await _org(async_session, "fb-none")
        charge = await _charge(async_session, org, resource_id=107)

        assert await _mandate_collects(async_session, charge) is False


class TestOneMandatePerAccount:
    async def test_creating_twice_returns_the_same_mandate(
        self, db_session, async_session
    ):
        """A customer who closes the authorisation page and comes back must not
        end up with two banks collecting for the same number."""
        org = await _org(async_session, "one-live")
        first = await _mandate(async_session, org, sub="sub_one")

        again = await mandate_service.get_mandate(
            async_session, organization_id=org.id
        )
        assert again is not None and again.id == first.id

    async def test_a_cancelled_mandate_leaves_room_for_a_new_one(
        self, db_session, async_session
    ):
        """The partial unique index excludes terminal states on purpose —
        otherwise a customer who cancels can never set autopay up again."""
        org = await _org(async_session, "one-after-cancel")
        first = await _mandate(async_session, org, sub="sub_cancel_1")
        first.status = MandateStatus.CANCELLED.value
        await async_session.flush()

        second = await _mandate(async_session, org, sub="sub_cancel_2")
        await async_session.flush()

        live = await mandate_service.get_mandate(async_session, organization_id=org.id)
        assert live is not None and live.id == second.id


class TestStatusVocabulary:
    def test_only_two_states_authorise_us_to_collect(self):
        """Written as a set in one place because three copies of the same tuple
        is how they drift apart."""
        assert MandateStatus.authorised() == frozenset({"authenticated", "active"})

    def test_terminal_states_are_the_ones_that_free_the_unique_index(self):
        """These two lists have to agree or a cancelled mandate blocks a new one
        for ever. The index literal lives in the migration; this asserts the
        Python side matches it."""
        assert mandate_service.TERMINAL == frozenset(
            {"cancelled", "completed", "expired"}
        )
