"""Closing a workspace: the owner asks, seven days pass, everything personal goes.

`erase_organization` removes what calls left behind. A customer who leaves is
owed more than that under the DPDP Act s12 and GDPR Art. 17: their contacts,
the files they taught their bots, the embeddings made from those files, the
memory their bots built up, their connected-app tokens, and their own sign-in.
This module is the whole of it, behind one owner action.

**Why a grace period.** Closing is irreversible and one click from an owner's
settings page. Seven days lets a mistaken or hostile click (a departing
employee with owner rights) be undone by anyone who notices, and the request
row records who asked and when either way.

**What is kept, on purpose.** The organization row, billing (payments, ledger,
invoices, tax documents: the law requires them for years), agreement
acceptances, erasure requests and audit logs (the evidence the obligation was
met), and KYC (telecom rules require its retention). A person's own login is
anonymised only if this was their only workspace; someone who also belongs
elsewhere keeps their account and simply loses this membership.

**Backups.** Encrypted database backups expire on their own schedule (30 days).
Erased data can survive in them until then; the privacy notice says so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    AgentEventModel,
    APIKeyModel,
    AppInteractionModel,
    ContactListModel,
    ContactModel,
    EmbedTokenModel,
    ErasureRequestModel,
    ExternalCredentialModel,
    GoogleCalendarConnectionModel,
    ImportedCallModel,
    InAppNotificationModel,
    KnowledgeBaseDocumentModel,
    MemberConnectionModel,
    MissedCallEventModel,
    OrganisationFactModel,
    OrganisationSkillDocumentModel,
    OrganizationInvitationModel,
    OrganizationMembershipModel,
    OrganizationProviderCredentialModel,
    UserModel,
    WorkflowModel,
)
from api.services.knowledge_base.upload_keys import KEY_ROOT
from api.services.privacy.erasure import erase_organization
from api.services.privacy.retention import _delete_objects

#: Time between the owner's request and the deletion.
GRACE_PERIOD = timedelta(days=7)

SUBJECT_TYPE = "workspace"
SCHEDULED = "scheduled"
CANCELLED = "cancelled"

#: Rows deleted outright, in this order. Personal data or credentials, none of
#: which a closed account has any use for. Knowledge chunks and embeddings go
#: with their documents (ON DELETE CASCADE).
PERSONAL_TABLES = (
    ContactModel,
    ContactListModel,
    ImportedCallModel,
    MissedCallEventModel,
    OrganisationFactModel,
    OrganisationSkillDocumentModel,
    AppInteractionModel,
    AgentEventModel,
    InAppNotificationModel,
    KnowledgeBaseDocumentModel,
    MemberConnectionModel,
    ExternalCredentialModel,
    OrganizationProviderCredentialModel,
    GoogleCalendarConnectionModel,
    APIKeyModel,
    EmbedTokenModel,
    OrganizationInvitationModel,
)


class ClosureError(ValueError):
    """The request cannot be made as asked; the message is for the owner."""


def _as_dict(row: ErasureRequestModel) -> dict:
    requested = row.requested_at or datetime.now(UTC)
    return {
        "id": row.id,
        "status": row.status,
        "requested_at": requested.isoformat(),
        "deletes_at": (requested + GRACE_PERIOD).isoformat(),
    }


async def pending(session: AsyncSession, *, organization_id: int) -> dict | None:
    """The scheduled closure for this workspace, if there is one."""
    row = await session.scalar(
        select(ErasureRequestModel).where(
            ErasureRequestModel.organization_id == organization_id,
            ErasureRequestModel.subject_type == SUBJECT_TYPE,
            ErasureRequestModel.status == SCHEDULED,
        )
    )
    return _as_dict(row) if row else None


async def schedule(
    session: AsyncSession, *, organization_id: int, requested_by: int
) -> dict:
    """Schedule the workspace for deletion after the grace period.

    Idempotent: asking twice returns the first request, so a double click does
    not restart the clock.
    """
    existing = await pending(session, organization_id=organization_id)
    if existing:
        return existing
    row = ErasureRequestModel(
        organization_id=organization_id,
        subject_type=SUBJECT_TYPE,
        status=SCHEDULED,
        requested_by=requested_by,
        requested_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    logger.info(
        "Workspace {} scheduled for deletion by user {}", organization_id, requested_by
    )
    return _as_dict(row)


async def cancel(session: AsyncSession, *, organization_id: int) -> bool:
    """Cancel a scheduled closure. False if there was none to cancel."""
    result = await session.execute(
        update(ErasureRequestModel)
        .where(
            ErasureRequestModel.organization_id == organization_id,
            ErasureRequestModel.subject_type == SUBJECT_TYPE,
            ErasureRequestModel.status == SCHEDULED,
        )
        .values(status=CANCELLED, completed_at=datetime.now(UTC))
    )
    return bool(result.rowcount)


async def _delete_knowledge_files(organization_id: int) -> tuple[int, int]:
    """Every stored upload under this workspace's prefix. Returns (deleted, failed)."""
    from api.services.storage import get_storage

    keys = await get_storage().alist_files(f"{KEY_ROOT}/{organization_id}/")
    deleted, failed = await _delete_objects(list(keys or []))
    return deleted, len(failed)


async def _release_people(session: AsyncSession, *, organization_id: int) -> int:
    """Remove every membership; anonymise the logins that belonged only here.

    Returns the number of logins anonymised. Staff accounts are never
    anonymised: they are ours, and belong to the platform, not the customer.
    """
    member_ids = (
        await session.scalars(
            select(OrganizationMembershipModel.user_id).where(
                OrganizationMembershipModel.organization_id == organization_id
            )
        )
    ).all()
    await session.execute(
        delete(OrganizationMembershipModel).where(
            OrganizationMembershipModel.organization_id == organization_id
        )
    )

    anonymised = 0
    for user_id in member_ids:
        user = await session.get(UserModel, user_id)
        if user is None:
            continue
        elsewhere = await session.scalar(
            select(OrganizationMembershipModel.organization_id)
            .where(OrganizationMembershipModel.user_id == user_id)
            .limit(1)
        )
        if elsewhere is not None:
            if user.selected_organization_id == organization_id:
                user.selected_organization_id = elsewhere
            continue
        if user.staff_role:
            continue
        user.email = None
        user.password_hash = None
        user.provider_id = f"deleted-{user.id}"
        user.email_verified_at = None
        user.mfa_enabled = False
        user.mfa_secret_encrypted = None
        user.mfa_recovery_hashes = None
        anonymised += 1
    return anonymised


async def close_workspace(
    session: AsyncSession, *, organization_id: int, requested_by: int | None = None
) -> dict:
    """Do the deletion now. Called by the nightly job once the grace period ends.

    Each table is deleted inside its own savepoint so one failure is recorded
    rather than undoing everything else; the summary lists what failed.
    """
    # Stop the bots first, so nothing new arrives while the rest is removed.
    await session.execute(
        update(WorkflowModel)
        .where(WorkflowModel.organization_id == organization_id)
        .values(is_live=False, status="archived")
    )

    calls = await erase_organization(
        session, organization_id=organization_id, requested_by=requested_by
    )
    files_deleted, files_failed = await _delete_knowledge_files(organization_id)

    rows_deleted = 0
    failed_tables: list[str] = []
    for model in PERSONAL_TABLES:
        try:
            async with session.begin_nested():
                result = await session.execute(
                    delete(model).where(model.organization_id == organization_id)
                )
                rows_deleted += result.rowcount or 0
        except Exception as exc:  # noqa: BLE001 - record and carry on
            logger.error(
                "Workspace {}: could not delete {}: {}",
                organization_id,
                model.__tablename__,
                exc,
            )
            failed_tables.append(model.__tablename__)

    anonymised = await _release_people(session, organization_id=organization_id)
    await session.flush()

    return {
        "runs_erased": calls.runs_affected,
        "objects_deleted": calls.objects_deleted + files_deleted,
        "objects_failed": files_failed,
        "rows_deleted": rows_deleted,
        "failed_tables": failed_tables,
        "logins_anonymised": anonymised,
        "complete": calls.status == "completed"
        and not files_failed
        and not failed_tables,
    }


async def run_due(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Close every workspace whose grace period has ended. Returns how many."""
    cutoff = (now or datetime.now(UTC)) - GRACE_PERIOD
    due = (
        await session.scalars(
            select(ErasureRequestModel).where(
                ErasureRequestModel.subject_type == SUBJECT_TYPE,
                ErasureRequestModel.status == SCHEDULED,
                ErasureRequestModel.requested_at <= cutoff,
            )
        )
    ).all()
    for request in due:
        summary = await close_workspace(
            session,
            organization_id=request.organization_id,
            requested_by=request.requested_by,
        )
        request.status = "completed" if summary["complete"] else "failed"
        request.completed_at = datetime.now(UTC)
        request.runs_affected = summary["runs_erased"]
        request.objects_deleted = summary["objects_deleted"]
        if not summary["complete"]:
            request.note = (
                f"{summary['objects_failed']} stored file(s) and tables "
                f"{summary['failed_tables'] or 'none'} could not be deleted; "
                "retry by rescheduling."
            )
        logger.info("Workspace {} closed: {}", request.organization_id, summary)
    await session.flush()
    return len(due)
