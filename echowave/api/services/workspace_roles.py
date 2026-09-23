"""A workspace's own roles: a customised agent saved, hired again, shared.

MP-2: an agent somebody has hired and tuned -- its prompts carrying their
business's answers, its steps and settings the way they left them -- is
saved as one of the workspace's own roles, and hired again from there
without answering anything twice. A snapshot, taken from the agent's draft
if it has one (what its owner last saw) and its published version if not.

MP-3: an admin shares a role by link, or copies it into another workspace
they belong to -- an agency setting up a client. The link carries an opaque
token shown once; only its sha256 is stored, as invitations do.

**Crossing a workspace boundary strips what belongs to the first
workspace.** A definition names tools, documents, recordings and
credentials by id, and each of those ids is the first workspace's row. The
rule is a blocklist by shape -- any key ending ``_uuid``, ``_uuids`` or
``recording_id``, plus the MCP filters keyed by tool id -- so a reference
kind added next year is removed too, not carried across by omission. What
is removed is written to ``needs`` and shown, never dropped silently.

Every function takes ``organization_id`` and filters by it; there is no
lookup of a role by id alone.
"""

from __future__ import annotations

import copy
import hashlib
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.models import (
    OrganizationMembershipModel,
    WorkflowDefinitionModel,
    WorkflowModel,
    WorkspaceRoleModel,
)
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole

#: Node-data keys that name another row of the first workspace.
_WORKSPACE_REF = re.compile(r"(_uuids?|recording_id)$")
#: Keyed by tool id rather than named like one.
_ALSO_WORKSPACE = {"mcp_tool_filters"}
#: Named like a reference, and not one: regenerated on every hire anyway.
_NOT_A_REF = {"trigger_path"}

_KINDS = {
    "tool_uuids": "tools",
    "document_uuids": "documents",
    "greeting_recording_id": "a greeting recording",
    "pre_call_fetch_credential_uuid": "a credential",
    "credential_uuid": "a credential",
    "mcp_tool_filters": "tools",
}


class RoleError(ValueError):
    """Something the person asked for cannot be done. The message says why."""


def enabled() -> bool:
    return bool(constants.WORKSPACE_ROLES_ENABLED)


def portable(
    definition: dict[str, Any], configurations: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, str]]]:
    """The definition and configurations with the first workspace's
    references removed, and what was removed, per step."""
    definition = copy.deepcopy(definition or {})
    needs: list[dict[str, str]] = []
    for node in definition.get("nodes") or []:
        data = node.get("data") or {}
        step = str(data.get("name") or node.get("id") or "a step")
        for key in list(data):
            if key in _NOT_A_REF:
                continue
            if not (_WORKSPACE_REF.search(key) or key in _ALSO_WORKSPACE):
                continue
            value = data.pop(key)
            if value:
                kind = _KINDS.get(key, key.replace("_", " "))
                if {"step": step, "kind": kind} not in needs:
                    needs.append({"step": step, "kind": kind})
    configurations = copy.deepcopy(configurations) if configurations else None
    if configurations:
        ambient = configurations.get("ambient_noise_configuration") or {}
        if ambient.get("storage_key"):
            # A custom background sound is a file in the first workspace's
            # storage; the copy falls back to the platform's own.
            configurations.pop("ambient_noise_configuration")
            needs.append({"step": "Settings", "kind": "a custom background sound"})
    return definition, configurations, needs


def _view(role: WorkspaceRoleModel) -> dict[str, Any]:
    return {
        "id": role.id,
        "name": role.name,
        "summary": role.summary,
        "template_id": role.template_id,
        "steps": len((role.definition or {}).get("nodes") or []),
        "needs": role.needs or [],
        "shared": role.share_token_hash is not None,
        "copied": role.source_role_id is not None,
        "created_at": role.created_at,
    }


async def _latest_definition(
    session: AsyncSession, workflow: WorkflowModel
) -> WorkflowDefinitionModel | None:
    draft = await session.scalar(
        select(WorkflowDefinitionModel)
        .where(
            WorkflowDefinitionModel.workflow_id == workflow.id,
            WorkflowDefinitionModel.status == "draft",
        )
        .order_by(WorkflowDefinitionModel.id.desc())
    )
    if draft is not None:
        return draft
    if workflow.released_definition_id:
        return await session.get(
            WorkflowDefinitionModel, workflow.released_definition_id
        )
    return await session.scalar(
        select(WorkflowDefinitionModel)
        .where(
            WorkflowDefinitionModel.workflow_id == workflow.id,
            WorkflowDefinitionModel.is_current.is_(True),
        )
        .order_by(WorkflowDefinitionModel.id.desc())
    )


async def save_from_workflow(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int | None,
    workflow_id: int,
    name: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    workflow = await session.scalar(
        select(WorkflowModel).where(
            WorkflowModel.id == workflow_id,
            WorkflowModel.organization_id == organization_id,
        )
    )
    if workflow is None:
        raise RoleError("That agent is not in this workspace.")
    version = await _latest_definition(session, workflow)
    definition = (
        version.workflow_json if version else None
    ) or workflow.workflow_definition
    if not (definition or {}).get("nodes"):
        raise RoleError("That agent has no steps to save yet.")
    configurations = (
        version.workflow_configurations if version else None
    ) or workflow.workflow_configurations
    template_id = (workflow.template_context_variables or {}).get("__template_id")
    role = WorkspaceRoleModel(
        organization_id=organization_id,
        name=(name or workflow.name or "Saved role").strip()[:128],
        summary=(summary or "").strip() or None,
        definition=copy.deepcopy(definition),
        configurations=copy.deepcopy(configurations) if configurations else None,
        template_id=template_id,
        source_workflow_id=workflow.id,
        created_by=user_id,
    )
    session.add(role)
    await session.flush()
    return _view(role)


async def list_for(
    session: AsyncSession, *, organization_id: int
) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(WorkspaceRoleModel)
        .where(WorkspaceRoleModel.organization_id == organization_id)
        .order_by(WorkspaceRoleModel.id.desc())
    )
    return [_view(row) for row in rows]


async def _get(
    session: AsyncSession, *, organization_id: int, role_id: int
) -> WorkspaceRoleModel:
    role = await session.scalar(
        select(WorkspaceRoleModel).where(
            WorkspaceRoleModel.organization_id == organization_id,
            WorkspaceRoleModel.id == role_id,
        )
    )
    if role is None:
        raise RoleError("That role is not in this workspace.")
    return role


async def remove(session: AsyncSession, *, organization_id: int, role_id: int) -> bool:
    result = await session.execute(
        delete(WorkspaceRoleModel).where(
            WorkspaceRoleModel.organization_id == organization_id,
            WorkspaceRoleModel.id == role_id,
        )
    )
    return bool(result.rowcount)


async def hire(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    role_id: int,
    agent_name: str | None = None,
    db=None,
) -> dict[str, Any]:
    """A fresh agent from the role, in the role's own workspace.

    Made the way duplicating an agent makes one -- ``create_workflow``, then
    its settings and provenance, then its triggers -- so the new agent has a
    handle, a first version and working trigger URLs. Not live while the role
    still needs something connected: an agent whose steps lost their tools
    on the way across would answer with half its job.
    """
    from api.services.workflow.duplicate import (
        _extract_trigger_paths,
        _regenerate_trigger_uuids,
    )

    role = await _get(session, organization_id=organization_id, role_id=role_id)
    if db is None:
        from api.db import db_client as db
    context = {"__workspace_role_id": role.id}
    if role.template_id:
        context["__template_id"] = role.template_id
    definition = _regenerate_trigger_uuids(copy.deepcopy(role.definition))
    configurations = copy.deepcopy(role.configurations) if role.configurations else None
    workflow = await db.create_workflow(
        name=(agent_name or role.name).strip()[:128],
        workflow_definition=definition,
        user_id=user_id,
        organization_id=organization_id,
        workflow_configurations=configurations,
        is_live=not role.needs,
    )
    await db.update_workflow(
        workflow_id=workflow.id,
        name=None,
        workflow_definition=None,
        template_context_variables=context,
        workflow_configurations=configurations,
        organization_id=organization_id,
    )
    trigger_paths = _extract_trigger_paths(definition)
    if trigger_paths:
        await db.sync_triggers_for_workflow(
            workflow_id=workflow.id,
            organization_id=organization_id,
            trigger_paths=trigger_paths,
        )
    return {
        "id": workflow.id,
        "name": workflow.name,
        "is_live": not role.needs,
        "needs": role.needs or [],
    }


# --- MP-3: sharing --------------------------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def share(session: AsyncSession, *, organization_id: int, role_id: int) -> str:
    """A new share link's token, shown once. Sharing again replaces the
    last link, so an old one stops working."""
    role = await _get(session, organization_id=organization_id, role_id=role_id)
    token = f"role_{secrets.token_urlsafe(24)}"
    role.share_token_hash = _hash(token)
    role.shared_at = datetime.now(timezone.utc)
    await session.flush()
    return token


async def unshare(session: AsyncSession, *, organization_id: int, role_id: int) -> None:
    role = await _get(session, organization_id=organization_id, role_id=role_id)
    role.share_token_hash = None
    role.shared_at = None
    await session.flush()


async def _by_token(session: AsyncSession, token: str) -> WorkspaceRoleModel:
    if not token or not token.startswith("role_"):
        raise RoleError("That link is not a shared role.")
    role = await session.scalar(
        select(WorkspaceRoleModel).where(
            WorkspaceRoleModel.share_token_hash == _hash(token)
        )
    )
    if role is None:
        raise RoleError("That link has been turned off, or was never valid.")
    return role


async def preview(session: AsyncSession, *, token: str) -> dict[str, Any]:
    """What a shared link offers, before installing: name, summary, steps,
    and what the receiving workspace will have to connect. Never the
    prompts -- those arrive only with the copy."""
    role = await _by_token(session, token)
    _, _, needs = portable(role.definition, role.configurations)
    return {
        "name": role.name,
        "summary": role.summary,
        "steps": len((role.definition or {}).get("nodes") or []),
        "needs": needs,
    }


async def _copy_into(
    session: AsyncSession,
    role: WorkspaceRoleModel,
    *,
    organization_id: int,
    user_id: int | None,
) -> dict[str, Any]:
    if role.organization_id == organization_id:
        raise RoleError("This role is already in this workspace.")
    definition, configurations, needs = portable(role.definition, role.configurations)
    copied = WorkspaceRoleModel(
        organization_id=organization_id,
        name=role.name,
        summary=role.summary,
        definition=definition,
        configurations=configurations,
        template_id=role.template_id,
        source_role_id=role.id,
        needs=needs,
        created_by=user_id,
    )
    session.add(copied)
    await session.flush()
    return _view(copied)


async def install(
    session: AsyncSession, *, token: str, organization_id: int, user_id: int | None
) -> dict[str, Any]:
    """Copy a shared role into the installer's own workspace."""
    role = await _by_token(session, token)
    return await _copy_into(
        session, role, organization_id=organization_id, user_id=user_id
    )


async def copy_to(
    session: AsyncSession,
    *,
    organization_id: int,
    role_id: int,
    target_organization_id: int,
    user_id: int,
) -> dict[str, Any]:
    """Copy a role into another workspace the same person belongs to -- an
    agency setting up a client. Their membership there is checked here,
    against the target, not taken from the request."""
    membership = await session.scalar(
        select(OrganizationMembershipModel).where(
            OrganizationMembershipModel.user_id == user_id,
            OrganizationMembershipModel.organization_id == target_organization_id,
        )
    )
    rank = ORGANIZATION_ROLE_RANK.get(membership.role if membership else "", -1)
    if rank < ORGANIZATION_ROLE_RANK[OrganizationRole.MEMBER.value]:
        raise RoleError("You are not a member of that workspace.")
    role = await _get(session, organization_id=organization_id, role_id=role_id)
    return await _copy_into(
        session, role, organization_id=target_organization_id, user_id=user_id
    )


__all__ = [
    "RoleError",
    "copy_to",
    "enabled",
    "hire",
    "install",
    "list_for",
    "portable",
    "preview",
    "remove",
    "save_from_workflow",
    "share",
    "unshare",
]
