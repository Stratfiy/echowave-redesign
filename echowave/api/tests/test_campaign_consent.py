"""A campaign does not dial until somebody confirms the list agreed to it.

The do-not-call check is ours and automatic; this is the customer's fact,
asked once per campaign and kept with it. What is guarded: the first start
without the tick is refused with a status the screen can act on, the tick
is recorded against the person who gave it, and a later start does not ask
again.
"""

import pytest

from api.db.models import (
    CampaignModel,
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
)
from api.enums import OrganizationRole
from api.services.campaign import consent


async def _campaign(session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    user.selected_organization_id = org.id
    session.add(
        OrganizationMembershipModel(
            user_id=user.id, organization_id=org.id, role=OrganizationRole.OWNER.value
        )
    )
    workflow = WorkflowModel(name=f"wf-{slug}", organization_id=org.id)
    session.add(workflow)
    await session.flush()
    campaign = CampaignModel(
        name=f"c-{slug}",
        organization_id=org.id,
        workflow_id=workflow.id,
        created_by=user.id,
        source_type="csv",
        source_id="list.csv",
        state="created",
    )
    session.add(campaign)
    await session.flush()
    return user, campaign


def test_the_statement_names_the_three_things():
    text = consent.STATEMENT.lower()
    assert "agreed to be called" in text
    assert "asked not to be called" in text
    assert "dlt" in text


@pytest.mark.asyncio
class TestTheService:
    async def test_a_start_without_the_tick_is_refused(self, db_session, async_session):
        user, campaign = await _campaign(async_session, "refused")
        with pytest.raises(consent.ConsentNotAttested):
            await consent.require_attested(
                campaign=campaign, user_id=user.id, attested_now=False
            )

    async def test_the_tick_is_recorded_against_the_person(
        self, db_session, async_session
    ):
        user, campaign = await _campaign(async_session, "recorded")
        await consent.require_attested(
            campaign=campaign, user_id=user.id, attested_now=True
        )
        from api.db import db_client

        stored = await db_client.get_campaign_by_id(campaign.id)
        assert stored.consent_attested_at is not None
        assert stored.consent_attested_by == user.id

    async def test_a_second_start_keeps_the_first_attestation(
        self, db_session, async_session
    ):
        user, campaign = await _campaign(async_session, "kept")
        first = await consent.attest(campaign_id=campaign.id, user_id=user.id)
        other = UserModel(provider_id="user-kept-other")
        async_session.add(other)
        await async_session.flush()
        second = await consent.attest(campaign_id=campaign.id, user_id=other.id)
        assert second.consent_attested_by == user.id
        assert second.consent_attested_at == first.consent_attested_at
        # And no tick is needed any more.
        await consent.require_attested(
            campaign=second, user_id=other.id, attested_now=False
        )


@pytest.mark.asyncio
class TestTheRoute:
    async def test_the_first_start_asks_with_a_428(
        self, db_session, async_session, test_client_factory, monkeypatch
    ):
        """Before quota, before telephony: the screen needs to know this is a
        tick and not a broken campaign."""
        user, campaign = await _campaign(async_session, "route")
        from api.db import db_client
        from api.routes import campaign as campaign_routes

        async def _configs(_org):
            return [object()]

        monkeypatch.setattr(db_client, "list_telephony_configurations", _configs)

        async with test_client_factory(user) as client:
            response = await client.post(f"/api/v1/campaign/{campaign.id}/start")
        assert response.status_code == 428
        assert "agreed to be called" in response.json()["detail"]
        assert campaign_routes.consent is consent
