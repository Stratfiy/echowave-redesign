"""MP-2 and MP-3: a customised agent saved as a workspace role, hired again,
and shared.

What must hold, in the order it can go wrong:

1. **Saving takes what the owner last saw** -- the draft, if there is one --
   and only from an agent in the saver's own workspace.
2. **Hiring answers nothing twice**: the new agent carries the role's steps,
   settings and provenance, with fresh trigger URLs.
3. **Nothing crosses a workspace boundary that belongs to the first
   workspace.** Tools, documents, recordings, credentials, a custom
   background sound -- removed by shape, so a reference kind added later is
   removed too, and every removal is listed as something to connect.
4. **A share link is a secret**: shown once, stored as a hash, replaced by
   sharing again, dead once turned off. Copying into another workspace
   needs membership there, checked against that workspace.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import constants
from api.db.models import (
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
    WorkflowDefinitionModel,
    WorkflowModel,
    WorkspaceRoleModel,
)
from api.services import workspace_roles
from api.services.workspace_roles import RoleError, portable

DEFINITION = {
    "nodes": [
        {
            "id": "1",
            "type": "startCall",
            "data": {
                "name": "Answer",
                "prompt": "You are the front desk for Narayani Dental.",
                "tool_uuids": ["tool-a"],
                "document_uuids": ["doc-a"],
                "greeting_recording_id": "rec-1",
            },
        },
        {
            "id": "2",
            "type": "agentNode",
            "data": {
                "name": "Book",
                "prompt": "Book the appointment.",
                "mcp_tool_filters": {"tool-a": ["create_event"]},
                "pre_call_fetch_credential_uuid": "cred-1",
                "some_future_uuid": "x-1",
            },
        },
        {
            "id": "3",
            "type": "trigger",
            "data": {"name": "Webhook", "trigger_path": "old-path"},
        },
    ],
    "edges": [],
}
CONFIGURATIONS = {
    "channel": "voice",
    "ambient_noise_configuration": {"storage_key": "ambient-noise/1/9/cafe.mp3"},
}


async def _workspace(session, slug: str, role: str = "owner"):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    user.selected_organization_id = org.id
    session.add(
        OrganizationMembershipModel(user_id=user.id, organization_id=org.id, role=role)
    )
    await session.flush()
    return org, user


async def _agent(session, org, user, *, draft: dict | None = None):
    workflow = WorkflowModel(
        name="Front desk",
        organization_id=org.id,
        user_id=user.id,
        status="active",
        workflow_definition={"nodes": [{"id": "old", "data": {"name": "Old"}}]},
        template_context_variables={"__template_id": "clinic_appointment"},
    )
    session.add(workflow)
    await session.flush()
    session.add(
        WorkflowDefinitionModel(
            workflow_id=workflow.id,
            workflow_json=draft or DEFINITION,
            workflow_configurations=CONFIGURATIONS,
            status="draft",
        )
    )
    await session.flush()
    return workflow


# --- 3. the boundary, first: it is the one that leaks ------------------------


def test_everything_that_names_the_first_workspace_is_removed_and_listed():
    definition, configurations, needs = portable(DEFINITION, CONFIGURATIONS)
    answer, book, trigger = (n["data"] for n in definition["nodes"])
    for data in (answer, book):
        leftover = [
            k
            for k in data
            if k.endswith(("_uuid", "_uuids", "recording_id"))
            or k == "mcp_tool_filters"
        ]
        assert leftover == [], leftover
    # The prompt -- the business's own answers -- is what the copy is for.
    assert answer["prompt"] == "You are the front desk for Narayani Dental."
    # Not a reference: regenerated on every hire.
    assert trigger["trigger_path"] == "old-path"
    assert "ambient_noise_configuration" not in configurations
    assert configurations["channel"] == "voice"
    kinds = {(n["step"], n["kind"]) for n in needs}
    assert {
        ("Answer", "tools"),
        ("Answer", "documents"),
        ("Answer", "a greeting recording"),
        ("Book", "tools"),
        ("Book", "a credential"),
        # A reference kind nobody has named yet is still removed, and shown.
        ("Book", "some future uuid"),
        ("Settings", "a custom background sound"),
    } <= kinds
    # The original is untouched.
    assert DEFINITION["nodes"][0]["data"]["tool_uuids"] == ["tool-a"]


# --- 1. saving ------------------------------------------------------------------


@pytest.mark.asyncio
class TestSaving:
    async def test_it_saves_what_the_owner_last_saw(self, async_session):
        org, user = await _workspace(async_session, "save")
        agent = await _agent(async_session, org, user)
        role = await workspace_roles.save_from_workflow(
            async_session,
            organization_id=org.id,
            user_id=user.id,
            workflow_id=agent.id,
            summary="Our front desk, tuned",
        )
        assert role["name"] == "Front desk" and role["steps"] == 3
        assert role["template_id"] == "clinic_appointment"
        row = await async_session.get(WorkspaceRoleModel, role["id"])
        # The draft, not the older definition on the workflow row -- and
        # within its own workspace nothing is removed.
        assert row.definition["nodes"][0]["data"]["tool_uuids"] == ["tool-a"]
        assert row.needs is None

    async def test_an_agent_in_another_workspace_cannot_be_saved(self, async_session):
        mine, me = await _workspace(async_session, "save-mine")
        theirs, them = await _workspace(async_session, "save-theirs")
        their_agent = await _agent(async_session, theirs, them)
        with pytest.raises(RoleError, match="not in this workspace"):
            await workspace_roles.save_from_workflow(
                async_session,
                organization_id=mine.id,
                user_id=me.id,
                workflow_id=their_agent.id,
            )

    async def test_roles_are_listed_only_in_their_own_workspace(self, async_session):
        mine, me = await _workspace(async_session, "list-mine")
        theirs, them = await _workspace(async_session, "list-theirs")
        await workspace_roles.save_from_workflow(
            async_session,
            organization_id=theirs.id,
            user_id=them.id,
            workflow_id=(await _agent(async_session, theirs, them)).id,
        )
        assert (
            await workspace_roles.list_for(async_session, organization_id=mine.id) == []
        )
        assert (
            len(
                await workspace_roles.list_for(async_session, organization_id=theirs.id)
            )
            == 1
        )


# --- 2. hiring ------------------------------------------------------------------


class FakeDb:
    def __init__(self):
        self.created, self.updated, self.synced = [], [], []

    async def create_workflow(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=501, name=kwargs["name"])

    async def update_workflow(self, **kwargs):
        self.updated.append(kwargs)

    async def sync_triggers_for_workflow(self, **kwargs):
        self.synced.append(kwargs)


@pytest.mark.asyncio
class TestHiring:
    async def test_a_hire_carries_the_role_and_answers_nothing_twice(
        self, async_session
    ):
        org, user = await _workspace(async_session, "hire")
        role = await workspace_roles.save_from_workflow(
            async_session,
            organization_id=org.id,
            user_id=user.id,
            workflow_id=(await _agent(async_session, org, user)).id,
        )
        db = FakeDb()
        hired = await workspace_roles.hire(
            async_session,
            organization_id=org.id,
            user_id=user.id,
            role_id=role["id"],
            agent_name="Front desk, Adyar",
            db=db,
        )
        assert hired == {
            "id": 501,
            "name": "Front desk, Adyar",
            "is_live": True,
            "needs": [],
        }
        created = db.created[0]
        assert created["organization_id"] == org.id
        assert created["workflow_definition"]["nodes"][0]["data"]["prompt"].startswith(
            "You are the front desk for Narayani Dental"
        )
        new_path = created["workflow_definition"]["nodes"][2]["data"]["trigger_path"]
        assert new_path != "old-path"
        assert db.synced[0]["trigger_paths"] == [new_path]
        assert db.updated[0]["template_context_variables"] == {
            "__workspace_role_id": role["id"],
            "__template_id": "clinic_appointment",
        }

    async def test_a_role_from_another_workspace_cannot_be_hired(self, async_session):
        mine, me = await _workspace(async_session, "hire-mine")
        theirs, them = await _workspace(async_session, "hire-theirs")
        role = await workspace_roles.save_from_workflow(
            async_session,
            organization_id=theirs.id,
            user_id=them.id,
            workflow_id=(await _agent(async_session, theirs, them)).id,
        )
        with pytest.raises(RoleError):
            await workspace_roles.hire(
                async_session,
                organization_id=mine.id,
                user_id=me.id,
                role_id=role["id"],
                db=FakeDb(),
            )


# --- 4. sharing -----------------------------------------------------------------


@pytest.mark.asyncio
class TestSharing:
    async def _role(self, session, slug):
        org, user = await _workspace(session, slug)
        role = await workspace_roles.save_from_workflow(
            session,
            organization_id=org.id,
            user_id=user.id,
            workflow_id=(await _agent(session, org, user)).id,
        )
        return org, user, role

    async def test_a_link_is_stored_only_as_a_hash(self, async_session):
        org, _, role = await self._role(async_session, "share-hash")
        token = await workspace_roles.share(
            async_session, organization_id=org.id, role_id=role["id"]
        )
        row = await async_session.get(WorkspaceRoleModel, role["id"])
        assert token.startswith("role_") and token not in row.share_token_hash
        assert len(row.share_token_hash) == 64

    async def test_a_link_previews_without_the_prompts(self, async_session):
        org, _, role = await self._role(async_session, "share-preview")
        token = await workspace_roles.share(
            async_session, organization_id=org.id, role_id=role["id"]
        )
        seen = await workspace_roles.preview(async_session, token=token)
        assert seen["name"] == "Front desk" and seen["steps"] == 3
        assert "Narayani" not in str(seen)
        assert {"step": "Answer", "kind": "tools"} in seen["needs"]

    async def test_installing_copies_it_across_the_boundary(self, async_session):
        org, _, role = await self._role(async_session, "share-src")
        other, someone = await _workspace(async_session, "share-dst")
        token = await workspace_roles.share(
            async_session, organization_id=org.id, role_id=role["id"]
        )
        copied = await workspace_roles.install(
            async_session, token=token, organization_id=other.id, user_id=someone.id
        )
        row = await async_session.get(WorkspaceRoleModel, copied["id"])
        assert row.organization_id == other.id and row.source_role_id == role["id"]
        assert "tool_uuids" not in row.definition["nodes"][0]["data"]
        assert copied["needs"] and copied["copied"]
        # Not live until what it needs is connected.
        hired = await workspace_roles.hire(
            async_session,
            organization_id=other.id,
            user_id=someone.id,
            role_id=copied["id"],
            db=FakeDb(),
        )
        assert hired["is_live"] is False

    async def test_sharing_again_turns_the_old_link_off_and_unsharing_turns_it_off(
        self, async_session
    ):
        org, _, role = await self._role(async_session, "share-rotate")
        first = await workspace_roles.share(
            async_session, organization_id=org.id, role_id=role["id"]
        )
        second = await workspace_roles.share(
            async_session, organization_id=org.id, role_id=role["id"]
        )
        with pytest.raises(RoleError, match="turned off"):
            await workspace_roles.preview(async_session, token=first)
        await workspace_roles.unshare(
            async_session, organization_id=org.id, role_id=role["id"]
        )
        with pytest.raises(RoleError, match="turned off"):
            await workspace_roles.preview(async_session, token=second)

    async def test_a_role_is_not_installed_into_its_own_workspace(self, async_session):
        org, user, role = await self._role(async_session, "share-self")
        token = await workspace_roles.share(
            async_session, organization_id=org.id, role_id=role["id"]
        )
        with pytest.raises(RoleError, match="already in this workspace"):
            await workspace_roles.install(
                async_session, token=token, organization_id=org.id, user_id=user.id
            )


@pytest.mark.asyncio
class TestCopyingIntoAClientsWorkspace:
    async def test_only_into_a_workspace_the_person_belongs_to(self, async_session):
        agency, owner = await _workspace(async_session, "agency")
        client, _ = await _workspace(async_session, "client")
        role = await workspace_roles.save_from_workflow(
            async_session,
            organization_id=agency.id,
            user_id=owner.id,
            workflow_id=(await _agent(async_session, agency, owner)).id,
        )
        with pytest.raises(RoleError, match="not a member"):
            await workspace_roles.copy_to(
                async_session,
                organization_id=agency.id,
                role_id=role["id"],
                target_organization_id=client.id,
                user_id=owner.id,
            )
        async_session.add(
            OrganizationMembershipModel(
                user_id=owner.id, organization_id=client.id, role="member"
            )
        )
        await async_session.flush()
        copied = await workspace_roles.copy_to(
            async_session,
            organization_id=agency.id,
            role_id=role["id"],
            target_organization_id=client.id,
            user_id=owner.id,
        )
        row = await async_session.get(WorkspaceRoleModel, copied["id"])
        assert row.organization_id == client.id
        assert "document_uuids" not in row.definition["nodes"][0]["data"]


# --- the flag and the permissions ------------------------------------------------


def test_every_route_is_hidden_while_the_flag_is_off(monkeypatch):
    from api.routes import workspace_roles as routes

    monkeypatch.setattr(constants, "WORKSPACE_ROLES_ENABLED", False)
    with pytest.raises(HTTPException) as caught:
        routes._enabled()
    assert caught.value.status_code == 404
    for route in routes.router.routes:
        assert any(d.call is routes._enabled for d in route.dependant.dependencies), (
            route.path
        )


def test_sharing_outside_the_workspace_is_an_admins():
    """Its prompts carry the business's own answers."""
    import inspect

    from api.routes import workspace_roles as routes

    for handler in (
        routes.share_workspace_role,
        routes.unshare_workspace_role,
        routes.copy_workspace_role,
    ):
        default = inspect.signature(handler).parameters["user"].default
        assert default.dependency is routes._admin, handler.__name__
    for handler in (
        routes.save_workspace_role,
        routes.hire_workspace_role,
        routes.install_shared_role,
    ):
        default = inspect.signature(handler).parameters["user"].default
        assert default.dependency is routes._member, handler.__name__
