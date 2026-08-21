"""A number Decibyl bought cannot be made to vanish by deleting its row.

`DELETE .../phone-numbers/{id}` used to hard-delete whatever row it was given,
with no check for `carrier_number_id` — the column that means we bought this
number and are paying the carrier rent for it every month. Deleting the row
does not release it at the carrier: nothing calls the provider, nothing closes
the recurring rental charge, and the row that was the only record we ever held
the number is simply gone. The result is an orphaned rental Decibyl keeps
paying with no trace of why, indefinitely.

`POST /managed-numbers/{id}/release` is the real release path — it calls the
carrier, closes the rental, and soft-deletes the row so the release itself
stays auditable. This route now refuses on any carrier-owned number and points
at that instead. A number the customer added by hand against their own carrier
account (`carrier_number_id` is null — we never bought it, never billed for
it) is unaffected and deletes exactly as before.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.db.models import OrganizationModel


async def _org(session, slug: str) -> int:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org.id


def _user(organization_id: int):
    return type("U", (), {"selected_organization_id": organization_id})()


@pytest.mark.asyncio
class TestCarrierOwnedNumbersRefuseDeletion:
    async def test_a_bought_number_is_refused(self, db_session, async_session):
        from api.db import db_client
        from api.routes import organization as organization_routes

        organization_id = await _org(async_session, "bought")
        config = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Decibyl managed",
            provider="plivo",
            credentials={"auth_id": "MAXXXXXXXXXXXXXXXXXX", "auth_token": "t"},
        )
        number = await db_client.create_phone_number(
            telephony_configuration_id=config.id,
            organization_id=organization_id,
            address="+919800000001",
            carrier_number_id="PNXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        )

        with pytest.raises(HTTPException) as excinfo:
            await organization_routes.delete_phone_number(
                config_id=config.id,
                phone_number_id=number.id,
                user=_user(organization_id),
            )

        assert excinfo.value.status_code == 409
        assert "bought through Decibyl" in excinfo.value.detail
        assert "release" in excinfo.value.detail.lower()

        # And the row must still be there — the refusal is not a silent no-op.
        still_there = await db_client.get_phone_number_for_config(number.id, config.id)
        assert still_there is not None

    async def test_a_released_number_is_still_refused(self, db_session, async_session):
        """Released rows are the audit trail. A carrier-owned number that has
        already been given back must still keep its row."""
        from api.db import db_client
        from api.enums import PhoneNumberStatus
        from api.routes import organization as organization_routes

        organization_id = await _org(async_session, "released")
        config = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Decibyl managed",
            provider="plivo",
            credentials={"auth_id": "MAXXXXXXXXXXXXXXXXXX", "auth_token": "t"},
        )
        number = await db_client.create_phone_number(
            telephony_configuration_id=config.id,
            organization_id=organization_id,
            address="+919800000002",
            carrier_number_id="PNXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
            status=PhoneNumberStatus.RELEASED.value,
        )

        with pytest.raises(HTTPException) as excinfo:
            await organization_routes.delete_phone_number(
                config_id=config.id,
                phone_number_id=number.id,
                user=_user(organization_id),
            )

        assert excinfo.value.status_code == 409

    async def test_a_hand_added_number_still_deletes(self, db_session, async_session):
        """The customer's own carrier account: never bought, never billed by
        us, and this route's original behaviour for it is unchanged."""
        from api.db import db_client
        from api.routes import organization as organization_routes

        organization_id = await _org(async_session, "hand-added")
        config = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Customer's own Twilio",
            provider="twilio",
            credentials={"account_sid": "AC1", "auth_token": "t"},
        )
        number = await db_client.create_phone_number(
            telephony_configuration_id=config.id,
            organization_id=organization_id,
            address="+919800000003",
        )

        result = await organization_routes.delete_phone_number(
            config_id=config.id,
            phone_number_id=number.id,
            user=_user(organization_id),
        )

        assert result == {"message": "Phone number deleted"}
        assert await db_client.get_phone_number_for_config(number.id, config.id) is None
