"""Privacy and security for one person (screen 25; handoff 24 "Privacy and
permissions").

The workspace's own data rights -- call retention, erasure of a caller's
number, the workspace export and closing the workspace -- already live in
``services/privacy`` and the Compliance screen; this module does not repeat
them. It adds what belongs to the *person*:

* **Export** of their own data: preferences, onboarding answers, their
  personal memory in every workspace they are in, their saved items, the
  feedback they gave, their temporary conversations. Built by the ARQ
  worker, downloadable for ``PERSONAL_EXPORT_DAYS``, then expired.
* **Deletion** of the same, store by store, through a card in their personal
  space (the controls card machinery; nobody else sees it). Irreversible,
  and the card says so. Every store's outcome is recorded with any
  exception that legitimately remains -- the sign-in account itself, and the
  knowledge graph where one is configured, need a person at Decibyl; the
  record says so rather than claiming they are gone.

The effective retention shown here is read from the same policy the purge
job enforces (``services/privacy/retention``), never a promise of its own.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import delete, func, select, update

from api import constants
from api.db import db_client
from api.db.controls_models import MemberPreferencesModel, OutputFeedbackModel
from api.db.models import AgentEventModel, OrganisationFactModel
from api.db.settings_models import (
    MemoryFactRevisionModel,
    PersonalDataRequestModel,
    SavedItemModel,
    TemporaryConversationModel,
)
from api.db.shell_models import UserOnboardingModel
from api.services import features
from api.services.settings import PRIVACY_CENTER

EXPORT = "export"
DELETION = "deletion"

#: What a person types to ask for deletion when they have no second factor.
DELETE_PHRASE = "delete my data"


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(PRIVACY_CENTER, organization_id)


class RequestNotFound(LookupError):
    pass


class PrivacyInvalid(ValueError):
    pass


class NeedsSetup(RuntimeError):
    """Something this needs is not on here; ``str(exc)`` says what."""


# --- the stores ---------------------------------------------------------------

#: Every store this module exports and deletes, in the order deletion runs.
#: A store added to a person's data must be added here, or it is neither
#: exported nor deleted -- ``test_settings_privacy`` lists the tables.
STORES = (
    ("preferences", "Your settings (language, timezone, voice, instructions)"),
    ("onboarding", "Your answers when you joined"),
    ("memory", "Your personal memory, in every workspace you are in"),
    ("saved_items", "Things you saved"),
    ("feedback", "Your Yes / Not quite answers"),
    ("temporary", "Your temporary conversations"),
    ("personal_space", "Conversations in your personal space"),
)

#: What is not deleted here, and why. Shown before deletion and kept on the
#: record after it.
EXCEPTIONS = (
    {
        "store": "sign_in",
        "label": "Your sign-in account",
        "exception": (
            "Kept until a person at Decibyl closes it, so you are not locked "
            "out halfway. We are told about the request."
        ),
    },
    {
        "store": "workspace_data",
        "label": "What you made for a team (agents, calls, documents)",
        "exception": "Belongs to that workspace; its owner can delete it.",
    },
    {
        "store": "backups",
        "label": "Backups",
        "exception": "Removed when the backups that hold it expire.",
    },
)


async def _counts(user_id: int) -> dict[str, int]:
    async with db_client.async_session() as session:
        space = await _personal_space_id(session, user_id)

        async def count(statement) -> int:
            return int(await session.scalar(statement) or 0)

        return {
            "preferences": await count(
                select(func.count()).where(MemberPreferencesModel.user_id == user_id)
            ),
            "onboarding": await count(
                select(func.count()).where(UserOnboardingModel.user_id == user_id)
            ),
            "memory": await count(
                select(func.count()).where(OrganisationFactModel.user_id == user_id)
            ),
            "saved_items": await count(
                select(func.count()).where(SavedItemModel.owner_user_id == user_id)
            ),
            "feedback": await count(
                select(func.count()).where(OutputFeedbackModel.user_id == user_id)
            ),
            "temporary": await count(
                select(func.count()).where(
                    TemporaryConversationModel.user_id == user_id,
                    TemporaryConversationModel.purged_at.is_(None),
                )
            ),
            "personal_space": (
                await count(
                    select(func.count()).where(AgentEventModel.organization_id == space)
                )
                if space
                else 0
            ),
        }


async def _personal_space_id(session: Any, user_id: int) -> int | None:
    from api.db.models import OrganizationModel

    found = await session.scalar(
        select(OrganizationModel.id).where(
            OrganizationModel.personal_owner_user_id == user_id,
            OrganizationModel.kind == "personal",
        )
    )
    return int(found) if found else None


async def preview(user_id: int, kind: str) -> dict[str, Any]:
    """Exactly what an export or a deletion would cover, with counts."""
    if kind not in (EXPORT, DELETION):
        raise PrivacyInvalid("Export or deletion.")
    counts = await _counts(user_id)
    stores = [
        {"store": key, "label": label, "count": counts.get(key, 0)}
        for key, label in STORES
    ]
    return {
        "kind": kind,
        "stores": stores,
        "exceptions": list(EXCEPTIONS) if kind == DELETION else [],
        "scope": "personal",
        "lines": (
            [
                "Only your own data. Your workspaces' data is under Compliance.",
                f"Ready to download for {constants.PERSONAL_EXPORT_DAYS} days.",
            ]
            if kind == EXPORT
            else [
                "Only your own data. Nothing of your workspaces is deleted.",
                "This cannot be undone.",
            ]
        ),
    }


def _row_view(row: PersonalDataRequestModel) -> dict[str, Any]:
    status = row.status
    if (
        row.kind == EXPORT
        and status == "ready"
        and row.expires_at
        and row.expires_at <= datetime.now(UTC)
    ):
        status = "expired"
    return {
        "id": int(row.id),
        "kind": row.kind,
        "status": status,
        "stores": list(row.stores or []),
        "card_event_id": row.card_event_id,
        "card_organization_id": row.organization_id,
        "error": row.error,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat(),
    }


async def requests_for(user_id: int) -> list[dict[str, Any]]:
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(PersonalDataRequestModel)
                .where(PersonalDataRequestModel.user_id == user_id)
                .order_by(PersonalDataRequestModel.id.desc())
                .limit(20)
            )
        ).scalars()
        return [_row_view(row) for row in rows]


async def _get(user_id: int, request_id: int) -> PersonalDataRequestModel:
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(PersonalDataRequestModel).where(
                PersonalDataRequestModel.id == request_id,
                PersonalDataRequestModel.user_id == user_id,
            )
        )
    if row is None:
        raise RequestNotFound
    return row


async def get(user_id: int, request_id: int) -> dict[str, Any]:
    return _row_view(await _get(user_id, request_id))


# --- export --------------------------------------------------------------------


async def request_export(user_id: int) -> dict[str, Any]:
    """Queue an export of the person's own data for the worker to build."""
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = PersonalDataRequestModel(
            user_id=user_id,
            kind=EXPORT,
            status="queued",
            stores=[],
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    try:
        await enqueue_job(FunctionNames.BUILD_PERSONAL_EXPORT, int(row.id))
    except Exception as exc:  # noqa: BLE001 - said on the record, not hidden
        logger.error("Personal export {} could not be queued: {}", row.id, exc)
        await _set(row.id, status="failed", error="Could not be started. Try again.")
    return await get(user_id, int(row.id))


async def _set(request_id: int, **values: Any) -> None:
    values["updated_at"] = datetime.now(UTC)
    async with db_client.async_session() as session:
        await session.execute(
            update(PersonalDataRequestModel)
            .where(PersonalDataRequestModel.id == request_id)
            .values(**values)
        )
        await session.commit()


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


async def _gather(user_id: int) -> dict[str, Any]:
    """Everything of the person's own, as plain data."""
    from api.services import member_preferences

    async with db_client.async_session() as session:
        onboarding = await session.get(UserOnboardingModel, user_id)
        facts = (
            await session.execute(
                select(OrganisationFactModel).where(
                    OrganisationFactModel.user_id == user_id
                )
            )
        ).scalars()
        saved = (
            await session.execute(
                select(SavedItemModel).where(SavedItemModel.owner_user_id == user_id)
            )
        ).scalars()
        feedback = (
            await session.execute(
                select(OutputFeedbackModel).where(
                    OutputFeedbackModel.user_id == user_id
                )
            )
        ).scalars()
        temporary = (
            await session.execute(
                select(TemporaryConversationModel).where(
                    TemporaryConversationModel.user_id == user_id
                )
            )
        ).scalars()
        return {
            "exported_at": datetime.now(UTC).isoformat(),
            "preferences": await member_preferences.get(user_id),
            "onboarding": (
                {
                    "language": onboarding.language,
                    "timezone": onboarding.timezone,
                    "preferred_name": onboarding.preferred_name,
                    "completed_at": _iso(onboarding.completed_at),
                }
                if onboarding
                else None
            ),
            "memory": [
                {
                    "workspace_id": f.organization_id,
                    "about": f"{f.subject_type}:{f.subject_key}",
                    "key": f.key,
                    "value": f.value,
                    "status": f.status,
                    "first_seen_at": _iso(f.first_seen_at),
                    "last_seen_at": _iso(f.last_seen_at),
                }
                for f in facts
            ],
            "saved_items": [
                {
                    "workspace_id": s.organization_id,
                    "kind": s.kind,
                    "title": s.title,
                    "body": s.body,
                    "visibility": s.visibility,
                    "status": s.status,
                    "created_at": _iso(s.created_at),
                }
                for s in saved
            ],
            "feedback": [
                {
                    "workspace_id": f.organization_id,
                    "on": f"{f.subject_kind}:{f.subject_id}",
                    "verdict": f.verdict,
                    "reasons": list(f.reasons or []),
                    "at": _iso(f.created_at),
                }
                for f in feedback
            ],
            "temporary_conversations": [
                {
                    "workspace_id": t.organization_id,
                    "started_at": _iso(t.created_at),
                    "deleted_at": _iso(t.purged_at),
                }
                for t in temporary
            ],
        }


async def build_export(request_id: int) -> None:
    """The worker's half: gather, store, mark ready. Never raises."""
    async with db_client.async_session() as session:
        row = await session.get(PersonalDataRequestModel, request_id)
    if row is None or row.kind != EXPORT or row.status != "queued" or not row.user_id:
        return
    await _set(request_id, status="running")
    try:
        payload = await _gather(int(row.user_id))
    except Exception as exc:  # noqa: BLE001 - the record must say something
        logger.error("Personal export {} failed: {}", request_id, exc)
        await _set(request_id, status="failed", error="Could not be built. Try again.")
        return
    stores = [
        {
            "store": key,
            "label": label,
            "count": (
                len(payload.get(key) or [])
                if isinstance(payload.get(key), list)
                else int(payload.get(key) is not None)
            ),
            "status": "included",
        }
        for key, label in STORES
        if key in payload
    ]
    now = datetime.now(UTC)
    await _set(
        request_id,
        status="ready",
        export_payload=payload,
        stores=stores,
        completed_at=now,
        expires_at=now + timedelta(days=constants.PERSONAL_EXPORT_DAYS),
    )


async def download(user_id: int, request_id: int) -> dict[str, Any]:
    row = await _get(user_id, request_id)
    view = _row_view(row)
    if row.kind != EXPORT:
        raise RequestNotFound
    if view["status"] == "expired":
        if row.status != "expired":
            await _set(request_id, status="expired", export_payload=None)
        raise PrivacyInvalid("This export has expired. Ask for a new one.")
    if row.status != "ready" or row.export_payload is None:
        raise PrivacyInvalid("This export is not ready yet.")
    return dict(row.export_payload)


# --- deletion ------------------------------------------------------------------


async def _identity_checked(user: Any, *, phrase: str | None, code: str | None) -> None:
    """A second factor when the person has one, the typed phrase otherwise
    (screen 25: "identity checks where required")."""
    if getattr(user, "mfa_enabled", False) and getattr(
        user, "mfa_secret_encrypted", None
    ):
        from api.services.auth import mfa

        counter = mfa.verify_code(
            secret=mfa.decrypt_secret(user.mfa_secret_encrypted),
            code=(code or "").strip(),
            last_counter=getattr(user, "mfa_last_counter", None),
        )
        if counter is None:
            raise PrivacyInvalid("Enter the current code from your authenticator app.")
        await db_client.update_user_mfa(user.id, mfa_last_counter=counter)
        return
    if (phrase or "").strip().lower() != DELETE_PHRASE:
        raise PrivacyInvalid(f"Type “{DELETE_PHRASE}” to confirm.")


async def request_deletion(
    user: Any, *, phrase: str | None = None, code: str | None = None
) -> dict[str, Any]:
    """Ask for the person's own data to be deleted. Nothing is deleted until
    they press Do it on the card this raises in their personal space."""
    from api.services import personal_space
    from api.services.settings import cards

    if not features.is_on(personal_space.FLAG):
        raise NeedsSetup(
            "Deleting your data asks for your OK in your personal space, "
            "which is not open here yet."
        )
    await _identity_checked(user, phrase=phrase, code=code)
    user_id = int(user.id)
    async with db_client.async_session() as session:
        waiting = await session.scalar(
            select(PersonalDataRequestModel.id).where(
                PersonalDataRequestModel.user_id == user_id,
                PersonalDataRequestModel.kind == DELETION,
                PersonalDataRequestModel.status.in_(
                    ("awaiting_approval", "queued", "running")
                ),
            )
        )
    if waiting:
        return await get(user_id, int(waiting))

    space = await personal_space.ensure(user_id)
    before = await preview(user_id, DELETION)
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = PersonalDataRequestModel(
            user_id=user_id,
            organization_id=int(space.id),
            kind=DELETION,
            status="awaiting_approval",
            stores=[{**store, "status": "pending"} for store in before["stores"]]
            + [{**e, "count": None, "status": "exception"} for e in EXCEPTIONS],
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    card = await cards.propose(
        organization_id=int(space.id),
        owner_user_id=user_id,
        action=cards.DELETE_PERSONAL_DATA,
        args={
            "request_id": int(row.id),
            "stores": [s["store"] for s in before["stores"]],
        },
        label="Delete your personal data",
        effect=(
            "Deletes your settings, onboarding answers, personal memory, saved "
            "items, feedback, temporary conversations and personal-space "
            "conversations. This cannot be undone."
        ),
        reversible=False,
    )
    await _set(int(row.id), card_event_id=card["event_id"])
    return {**(await get(user_id, int(row.id))), "card": card}


async def run_deletion(*, user_id: int, request_id: int) -> str:
    """The card's run: each store in turn, each outcome recorded. A store
    that fails does not stop the others; the request ends ``complete`` or
    ``partial_failure`` and says which."""
    row = await _get(user_id, request_id)
    if row.kind != DELETION:
        raise RequestNotFound
    if row.status not in ("awaiting_approval", "queued"):
        return "Already done."
    await _set(request_id, status="running")
    stores: list[dict[str, Any]] = []
    failed = 0
    for key, label in STORES:
        try:
            removed = await _delete_store(
                key, user_id=user_id, keep_event_id=row.card_event_id
            )
            stores.append(
                {"store": key, "label": label, "count": removed, "status": "deleted"}
            )
        except Exception as exc:  # noqa: BLE001 - recorded per store
            failed += 1
            logger.error("Deleting {} for request {} failed: {}", key, request_id, exc)
            stores.append(
                {
                    "store": key,
                    "label": label,
                    "count": None,
                    "status": "failed",
                    "exception": "Could not be deleted yet; we will retry with a person.",
                }
            )
    stores.extend({**e, "count": None, "status": "exception"} for e in EXCEPTIONS)
    graph = await _graph_note()
    if graph:
        stores.append(graph)
    status = "partial_failure" if failed else "complete"
    await _set(
        request_id,
        status=status,
        stores=stores,
        completed_at=datetime.now(UTC),
        error=f"{failed} could not be deleted yet." if failed else None,
    )
    if failed:
        return f"Deleted most of your data; {failed} part(s) could not be deleted yet."
    return "Your personal data is deleted. The exceptions are listed in Settings."


async def _graph_note() -> dict[str, Any] | None:
    from api.services.knowledge_graph.client import graph_is_configured

    if not graph_is_configured():
        return None
    return {
        "store": "knowledge_graph",
        "label": "What the knowledge graph learned from your conversations",
        "count": None,
        "status": "exception",
        "exception": "Removed by a person at Decibyl; automatic removal is not built yet.",
    }


async def _delete_store(key: str, *, user_id: int, keep_event_id: int | None) -> int:
    async with db_client.async_session() as session:
        if key == "preferences":
            result = await session.execute(
                delete(MemberPreferencesModel).where(
                    MemberPreferencesModel.user_id == user_id
                )
            )
        elif key == "onboarding":
            result = await session.execute(
                delete(UserOnboardingModel).where(
                    UserOnboardingModel.user_id == user_id
                )
            )
        elif key == "memory":
            ids = [
                (r[0], r[1])
                for r in (
                    await session.execute(
                        select(
                            OrganisationFactModel.id,
                            OrganisationFactModel.organization_id,
                        ).where(OrganisationFactModel.user_id == user_id)
                    )
                ).all()
            ]
            for fact_id, organization_id in ids:
                await session.execute(
                    delete(MemoryFactRevisionModel).where(
                        MemoryFactRevisionModel.organization_id == organization_id,
                        MemoryFactRevisionModel.fact_id == fact_id,
                    )
                )
            result = await session.execute(
                delete(OrganisationFactModel).where(
                    OrganisationFactModel.user_id == user_id
                )
            )
        elif key == "saved_items":
            result = await session.execute(
                delete(SavedItemModel).where(SavedItemModel.owner_user_id == user_id)
            )
        elif key == "feedback":
            result = await session.execute(
                delete(OutputFeedbackModel).where(
                    OutputFeedbackModel.user_id == user_id
                )
            )
        elif key == "temporary":
            rows = list(
                (
                    await session.execute(
                        select(TemporaryConversationModel).where(
                            TemporaryConversationModel.user_id == user_id
                        )
                    )
                ).scalars()
            )
            for temp in rows:
                await session.execute(
                    delete(AgentEventModel).where(
                        AgentEventModel.organization_id == temp.organization_id,
                        AgentEventModel.thread_id == temp.thread_id,
                    )
                )
            result = await session.execute(
                delete(TemporaryConversationModel).where(
                    TemporaryConversationModel.user_id == user_id
                )
            )
        elif key == "personal_space":
            space = await _personal_space_id(session, user_id)
            if not space:
                return 0
            statement = delete(AgentEventModel).where(
                AgentEventModel.organization_id == space
            )
            if keep_event_id is not None:
                # The card running this deletion is its own record.
                statement = statement.where(AgentEventModel.id != keep_event_id)
            result = await session.execute(statement)
        else:
            raise PrivacyInvalid(key)
        await session.commit()
        return int(result.rowcount or 0)


# --- the overview ----------------------------------------------------------------


async def overview(user: Any, organization_id: int) -> dict[str, Any]:
    """What the screen shows above the actions."""
    from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole
    from api.services import personal_space
    from api.services.privacy import retention

    async with db_client.async_session() as session:
        policy = await retention.resolve_policy(
            session, organization_id=organization_id
        )
    membership = await db_client.get_membership(int(user.id), organization_id)
    role = getattr(membership, "role", None) or ""
    admin = (
        ORGANIZATION_ROLE_RANK.get(role, -1)
        >= ORGANIZATION_ROLE_RANK[OrganizationRole.ADMIN.value]
    )
    organization = await db_client.get_organization_by_id(organization_id)
    return {
        "retention": {
            "recording_days": policy.recording_days,
            "transcript_days": policy.transcript_days,
            "is_platform_default": policy.is_default,
            "temporary_conversation_hours": constants.TEMPORARY_CONVERSATION_HOURS,
            "export_days": constants.PERSONAL_EXPORT_DAYS,
            "managed_by_workspace": not admin,
            "workspace_name": getattr(organization, "name", None) or "Your workspace",
        },
        "security": {
            "mfa_enabled": bool(getattr(user, "mfa_enabled", False)),
            "has_password": bool(getattr(user, "password_hash", None)),
        },
        "deletion_available": features.is_on(personal_space.FLAG),
        "workspace_owner": role == OrganizationRole.OWNER.value,
        "delete_phrase": DELETE_PHRASE,
        "requests": await requests_for(int(user.id)),
    }
