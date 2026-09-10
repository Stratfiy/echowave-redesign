"""Staff can answer "what did this account agree to, and who confirmed the list"."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from api.db.models import (
    AgreementAcceptanceModel,
    CampaignModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
)
from api.enums import StaffRole


def _staff_client(user):
    """The staff dependency reads the request itself, so it is overridden
    directly rather than through the ordinary user override."""
    from api.app import app
    from api.services.auth.depends import get_superuser

    @asynccontextmanager
    async def _ctx():
        async def _override():
            return user

        app.dependency_overrides[get_superuser] = _override
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_superuser, None)

    return _ctx()


@pytest.mark.asyncio
async def test_the_audit_lists_acceptances_and_attestations(db_session, async_session):
    org = OrganizationModel(provider_id="org-audit", quota_decibyl_tokens=0)
    staff = UserModel(
        provider_id="user-audit-staff", staff_role=StaffRole.SUPERADMIN.value
    )
    member = UserModel(provider_id="user-audit-member", email="m@example.com")
    async_session.add_all([org, staff, member])
    await async_session.flush()
    org_id, member_id = org.id, member.id
    async_session.add(
        AgreementAcceptanceModel(
            organization_id=org_id,
            user_id=member_id,
            agreement="terms",
            version="2026-07",
            ip_address="10.0.0.1",
        )
    )
    workflow = WorkflowModel(name="wf-audit", organization_id=org_id)
    async_session.add(workflow)
    await async_session.flush()
    async_session.add(
        CampaignModel(
            name="September",
            organization_id=org_id,
            workflow_id=workflow.id,
            created_by=member_id,
            source_type="csv",
            source_id="x.csv",
            state="created",
            consent_attested_at=datetime.now(UTC),
            consent_attested_by=member_id,
        )
    )
    await async_session.flush()

    async with _staff_client(staff) as client:
        response = await client.get(f"/api/v1/admin/billing/accounts/{org_id}/consent")

    assert response.status_code == 200
    body = response.json()
    assert [
        (a["agreement"], a["version"], a["user_email"], a["ip_address"])
        for a in body["agreements"]
    ] == [("terms", "2026-07", "m@example.com", "10.0.0.1")]
    assert body["agreements"][0]["current"] is True
    assert [(c["name"], c["attested_by_email"]) for c in body["campaigns"]] == [
        ("September", "m@example.com")
    ]


@pytest.mark.asyncio
async def test_it_is_staff_only(db_session, async_session, test_client_factory):
    org = OrganizationModel(provider_id="org-audit-nostaff", quota_decibyl_tokens=0)
    user = UserModel(provider_id="user-audit-nostaff")
    async_session.add_all([org, user])
    await async_session.flush()
    async with test_client_factory(user) as client:
        response = await client.get(f"/api/v1/admin/billing/accounts/{org.id}/consent")
    # The staff dependency reads the request itself, so a non-staff session
    # is refused as unauthenticated rather than forbidden. Either is refused.
    assert response.status_code in (401, 403)
