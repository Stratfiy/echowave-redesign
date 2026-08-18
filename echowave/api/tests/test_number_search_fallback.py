"""What a search returns when the carrier has nothing matching.

Indian local inventory is thin, and asking for a city *and* a digit pattern
usually returns nothing. "Nothing available matching that right now" is a dead
end: it tells a customer nothing about what they could actually buy, on the one
screen whose job is to sell them something.

So a filtered search that finds nothing retries without the filters and says
so. The saying-so is not decoration — a list presented as matching a search it
did not match is worse than no list.
"""

from __future__ import annotations

import pytest

from api.db.models import OrganizationKycModel, OrganizationModel
from api.enums import KycStatus
from api.services.telephony.base import AvailableNumber


def _number(address: str, city: str | None = "Mumbai") -> AvailableNumber:
    return AvailableNumber(
        number=address,
        country_iso="IN",
        number_type="local",
        city=city,
        region="MH",
        monthly_rental="349",
        setup_price="0",
    )


async def _approved_org(session, slug: str) -> int:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    session.add(
        OrganizationKycModel(
            organization_id=org.id,
            status=KycStatus.CARRIER_APPROVED.value,
            carrier="plivo",
            carrier_reference="app-1",
        )
    )
    await session.flush()
    return org.id


@pytest.mark.asyncio
class TestWhenTheFiltersMatchNothing:
    async def test_it_falls_back_to_whatever_is_available(
        self, db_session, async_session, monkeypatch
    ):
        from api.db import db_client
        from api.routes import managed_numbers
        from api.services.telephony import provisioning

        organization_id = await _approved_org(async_session, "fallback")
        config = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Decibyl managed",
            provider="plivo",
            credentials={"auth_id": "MAXXXXXXXXXXXXXXXXXX", "auth_token": "t"},
        )

        calls = []

        async def _search(**kwargs):
            calls.append(kwargs)
            if kwargs.get("city") or kwargs.get("pattern"):
                return []
            return [_number("+912248901234")]

        monkeypatch.setattr(provisioning, "search", _search)

        response = await managed_numbers.search_numbers(
            managed_numbers.NumberSearchRequest(
                telephony_configuration_id=config.id,
                city="Nowhere",
                pattern="9999",
            ),
            user=type("U", (), {"selected_organization_id": organization_id})(),
        )

        assert response["exact_match"] is False
        assert [n["number"] for n in response["numbers"]] == ["+912248901234"]
        assert len(calls) == 2

    async def test_a_search_that_matched_is_reported_as_exact(
        self, db_session, async_session, monkeypatch
    ):
        from api.db import db_client
        from api.routes import managed_numbers
        from api.services.telephony import provisioning

        organization_id = await _approved_org(async_session, "exact")
        config = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Decibyl managed",
            provider="plivo",
            credentials={"auth_id": "MAXXXXXXXXXXXXXXXXXX", "auth_token": "t"},
        )

        async def _search(**_kwargs):
            return [_number("+912248901234")]

        monkeypatch.setattr(provisioning, "search", _search)

        response = await managed_numbers.search_numbers(
            managed_numbers.NumberSearchRequest(
                telephony_configuration_id=config.id, city="Mumbai"
            ),
            user=type("U", (), {"selected_organization_id": organization_id})(),
        )

        assert response["exact_match"] is True

    async def test_an_unfiltered_search_never_claims_a_fallback(
        self, db_session, async_session, monkeypatch
    ):
        """With no filters there is nothing to fall back *from*, so an empty
        result is simply empty and must not be dressed up as a near miss."""
        from api.db import db_client
        from api.routes import managed_numbers
        from api.services.telephony import provisioning

        organization_id = await _approved_org(async_session, "unfiltered")
        config = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Decibyl managed",
            provider="plivo",
            credentials={"auth_id": "MAXXXXXXXXXXXXXXXXXX", "auth_token": "t"},
        )

        calls = []

        async def _search(**kwargs):
            calls.append(kwargs)
            return []

        monkeypatch.setattr(provisioning, "search", _search)

        response = await managed_numbers.search_numbers(
            managed_numbers.NumberSearchRequest(telephony_configuration_id=config.id),
            user=type("U", (), {"selected_organization_id": organization_id})(),
        )

        assert response["exact_match"] is True
        assert response["numbers"] == []
        assert len(calls) == 1

    async def test_a_failed_retry_returns_the_honest_empty_result(
        self, db_session, async_session, monkeypatch
    ):
        """The unfiltered retry is a courtesy. If it fails, the customer sees
        the empty result their own search got — not an error about a query they
        never made."""
        from api.db import db_client
        from api.routes import managed_numbers
        from api.services.telephony import provisioning

        organization_id = await _approved_org(async_session, "retry-fails")
        config = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Decibyl managed",
            provider="plivo",
            credentials={"auth_id": "MAXXXXXXXXXXXXXXXXXX", "auth_token": "t"},
        )

        async def _search(**kwargs):
            if kwargs.get("city"):
                return []
            raise ValueError("carrier is down")

        monkeypatch.setattr(provisioning, "search", _search)

        response = await managed_numbers.search_numbers(
            managed_numbers.NumberSearchRequest(
                telephony_configuration_id=config.id, city="Mumbai"
            ),
            user=type("U", (), {"selected_organization_id": organization_id})(),
        )

        assert response["numbers"] == []
