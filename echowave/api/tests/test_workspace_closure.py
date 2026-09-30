"""Closing a workspace: asked by the owner, done after seven days, complete.

The tests that matter: nothing is deleted before the grace period; a cancelled
request deletes nothing; when it runs, contacts, knowledge files and logins go
while billing and the evidence of the request stay; and somebody who also
belongs to another workspace keeps their login.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from api.db.models import (
    ContactListModel,
    ContactModel,
    ErasureRequestModel,
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
)
from api.services.privacy import workspace_closure


class FakeStorage:
    def __init__(self, files: list[str]):
        self.files = list(files)
        self.deleted: list[str] = []

    async def alist_files(self, prefix: str) -> list[str]:
        return [f for f in self.files if f.startswith(prefix)]

    async def adelete_file(self, key: str) -> bool:
        self.deleted.append(key)
        return True


@pytest.fixture
def storage():
    fake = FakeStorage(
        ["knowledge_base/{org}/doc/file.pdf", "knowledge_base/999999/doc/other.pdf"]
    )
    with patch("api.services.storage.get_storage", return_value=fake):
        yield fake


async def _workspace(session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    owner = UserModel(provider_id=f"user-{slug}", email=f"{slug}@example.com")
    session.add_all([org, owner])
    await session.flush()
    owner.selected_organization_id = org.id
    session.add(
        OrganizationMembershipModel(
            user_id=owner.id, organization_id=org.id, role="owner"
        )
    )
    workflow = WorkflowModel(
        name=f"wf-{slug}",
        user_id=owner.id,
        organization_id=org.id,
        workflow_definition={},
        template_context_variables={},
        call_disposition_codes={},
    )
    contacts = ContactListModel(organization_id=org.id, name="Leads")
    session.add_all([workflow, contacts])
    await session.flush()
    session.add(
        ContactModel(
            organization_id=org.id,
            contact_list_id=contacts.id,
            phone_raw="+919800000000",
            phone_normalized="+919800000000",
            name="A Customer",
        )
    )
    await session.flush()
    return org, owner, workflow


async def _count(session, model, organization_id):
    return await session.scalar(
        select(func.count())
        .select_from(model)
        .where(model.organization_id == organization_id)
    )


@pytest.mark.asyncio
class TestWorkspaceClosure:
    async def test_nothing_is_deleted_during_the_grace_period(
        self, async_session, storage
    ):
        org, owner, _ = await _workspace(async_session, "grace")
        await workspace_closure.schedule(
            async_session, organization_id=org.id, requested_by=owner.id
        )

        closed = await workspace_closure.run_due(
            async_session, now=datetime.now(UTC) + timedelta(days=6)
        )

        assert closed == 0
        assert await _count(async_session, ContactModel, org.id) == 1

    async def test_asking_twice_does_not_restart_the_clock(
        self, async_session, storage
    ):
        org, owner, _ = await _workspace(async_session, "twice")
        first = await workspace_closure.schedule(
            async_session, organization_id=org.id, requested_by=owner.id
        )
        second = await workspace_closure.schedule(
            async_session, organization_id=org.id, requested_by=owner.id
        )
        assert first["id"] == second["id"]

    async def test_a_cancelled_request_deletes_nothing(self, async_session, storage):
        org, owner, _ = await _workspace(async_session, "cancel")
        await workspace_closure.schedule(
            async_session, organization_id=org.id, requested_by=owner.id
        )
        assert await workspace_closure.cancel(async_session, organization_id=org.id)

        closed = await workspace_closure.run_due(
            async_session, now=datetime.now(UTC) + timedelta(days=8)
        )

        assert closed == 0
        assert (
            await workspace_closure.pending(async_session, organization_id=org.id)
            is None
        )
        assert await _count(async_session, ContactModel, org.id) == 1

    async def test_after_seven_days_personal_data_and_logins_go(
        self, async_session, storage
    ):
        org, owner, workflow = await _workspace(async_session, "close")
        storage.files = [f.replace("{org}", str(org.id)) for f in storage.files]
        await workspace_closure.schedule(
            async_session, organization_id=org.id, requested_by=owner.id
        )

        closed = await workspace_closure.run_due(
            async_session, now=datetime.now(UTC) + timedelta(days=8)
        )

        assert closed == 1
        assert await _count(async_session, ContactModel, org.id) == 0
        assert await _count(async_session, ContactListModel, org.id) == 0
        assert await _count(async_session, OrganizationMembershipModel, org.id) == 0
        # Only this workspace's files, never another's.
        assert storage.deleted == [f"knowledge_base/{org.id}/doc/file.pdf"]
        # The bots stop.
        await async_session.refresh(workflow)
        assert workflow.is_live is False
        # A login that belonged only here is anonymised.
        await async_session.refresh(owner)
        assert owner.email is None
        assert owner.password_hash is None
        # The organization and the evidence of the request remain.
        assert await async_session.get(OrganizationModel, org.id) is not None
        request = await async_session.scalar(
            select(ErasureRequestModel).where(
                ErasureRequestModel.organization_id == org.id,
                ErasureRequestModel.subject_type == "workspace",
            )
        )
        assert request.status == "completed"

    async def test_someone_in_another_workspace_keeps_their_login(
        self, async_session, storage
    ):
        org, owner, _ = await _workspace(async_session, "leaver")
        other, _, _ = await _workspace(async_session, "stayer")
        async_session.add(
            OrganizationMembershipModel(
                user_id=owner.id, organization_id=other.id, role="member"
            )
        )
        await async_session.flush()
        await workspace_closure.schedule(
            async_session, organization_id=org.id, requested_by=owner.id
        )

        await workspace_closure.run_due(
            async_session, now=datetime.now(UTC) + timedelta(days=8)
        )

        await async_session.refresh(owner)
        assert owner.email == "leaver@example.com"
        assert owner.selected_organization_id == other.id
