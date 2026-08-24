"""The number-rental money path, exercised against a production-shaped session.

Numbers bought through the platform are the only telephony Decibyl charges for
today, so this path *is* the telephony revenue. Two places in it load an ORM row,
commit, and then read the row back:

* ``charge_period`` on the insufficient-balance branch — the dunning path, which
  is exactly the branch a customer who cannot pay their rent takes;
* ``close_rental`` — the release path, whose log line reads ``charge.id`` after
  the commit that ended the billing.

Both are invisible to the rest of the suite because ``conftest``'s session sets
``expire_on_commit=False`` while production's ``async_sessionmaker`` takes the
default. These tests therefore let the code open its **own** session, which is
the only way the expiry actually happens.


Every test here takes ``async_session`` and never uses it. That fixture creates
the schema and orders this file against the rest of the suite; without it these
tests pass alone and fail in a full run. It is *not* ``db_session``, which would
repoint ``db_client`` at the savepoint and hide the bug again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from api.db.models import (
    OrganizationModel,
    RecurringChargeModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
)
from api.enums import RecurringChargeStatus, RecurringChargeType


async def _account_with_an_unpayable_rental():
    """An organization holding a number it has no balance to pay for."""
    from api.db import db_client

    tag = uuid.uuid4().hex[:8]
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"org_rental_probe_{tag}")
        session.add(org)
        await session.flush()

        config = TelephonyConfigurationModel(
            organization_id=org.id, provider="plivo", name=f"probe-{tag}"
        )
        session.add(config)
        await session.flush()

        number = TelephonyPhoneNumberModel(
            organization_id=org.id,
            telephony_configuration_id=config.id,
            address=f"+9199000{tag[:5]}",
            address_normalized=f"+9199000{tag[:5]}",
            address_type="phone",
            country_code="IN",
        )
        session.add(number)
        await session.flush()

        now = datetime.now(UTC)
        charge = RecurringChargeModel(
            organization_id=org.id,
            charge_type=RecurringChargeType.NUMBER_RENTAL.value,
            resource_id=number.id,
            price_paise=49_900,
            cost_paise=20_000,
            status=RecurringChargeStatus.ACTIVE.value,
            started_at=now - timedelta(days=40),
            next_charge_at=now - timedelta(days=1),
        )
        session.add(charge)
        await session.flush()
        ids = (charge.id, number.id)
        await session.commit()
    return ids


async def test_an_unpayable_rental_reports_rather_than_raising(async_session):
    """The balance is zero, so this takes the dunning branch — which loads the
    charge and the number, commits, and then reads both back."""
    from api.services.billing import rentals

    charge_id, _ = await _account_with_an_unpayable_rental()

    outcome = await rentals.charge_period(charge_id=charge_id)

    assert outcome.reason == "insufficient_balance", outcome
    # The two attributes read after the commit. Reaching them at all is the
    # assertion; a MissingGreenlet here is the bug.
    assert outcome.organization_id is not None
    assert outcome.charged is False


async def test_closing_a_rental_survives_its_own_commit(async_session):
    """``close_rental`` ends the billing, commits, then logs ``charge.id``."""
    from api.services.billing import rentals

    _, phone_number_id = await _account_with_an_unpayable_rental()

    await rentals.close_rental(
        phone_number_id=phone_number_id,
        status=RecurringChargeStatus.RELEASED,
    )

    from sqlalchemy import select

    from api.db import db_client

    async with db_client.async_session() as session:
        row = await session.scalar(
            select(RecurringChargeModel).where(
                RecurringChargeModel.resource_id == phone_number_id
            )
        )
        assert row is not None
        assert row.status == RecurringChargeStatus.RELEASED.value
