"""The audit log (KAN-160, E-1): who did what to something that matters.

An enterprise buyer asks two questions of a system that acts for them:
who approved this, and what changed. The timeline answers the first for
the person reading a thread; it does not answer the second, and it is a
conversation, not a record. This is the record: one append-only row per
act, with the actor, the subject, what it was before and after, and it is
exported whole as CSV.

**Writing never raises.** The act it describes has already happened; a
log that could unwind a publish would be a worse bug than a missing row.
**Nothing is edited or deleted here.** There is no update and no delete,
which is the whole of what makes it an audit log.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.models import AuditEntryModel

# The acts recorded. A name here is a promise the export keeps.
CARD_CONFIRMED = "card_confirmed"
CARD_DECLINED = "card_declined"
CARD_UNDONE = "card_undone"
#: A person edited a waiting card; its approval no longer stands.
CARD_REVISED = "card_revised"
DECISION_MADE = "decision_made"
DOCUMENT_STATUS = "document_status"
DOCUMENT_APPROVED = "document_approved"
AGENT_PUBLISHED = "agent_published"
AGENT_LIVE = "agent_live"
AGENT_VISIBILITY = "agent_visibility"
PREFERENCES_SAVED = "preferences_saved"
RULES_SAVED = "approval_rules_saved"

COLUMNS = (
    "at",
    "actor",
    "actor_user_id",
    "action",
    "subject_kind",
    "subject_id",
    "subject",
    "before",
    "after",
    "note",
)


def _brief(value: Any) -> Any:
    """JSON-safe and bounded: an audit row is a fact, not a dump."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value if not isinstance(value, str) else value[:2000]
    if isinstance(value, dict):
        return {str(k)[:64]: _brief(v) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_brief(v) for v in list(value)[:40]]
    return str(value)[:2000]


async def record(
    organization_id: int,
    *,
    action: str,
    subject_kind: str,
    subject_id: Any = None,
    subject: str | None = None,
    actor_user_id: int | None = None,
    actor: str | None = None,
    before: Any = None,
    after: Any = None,
    note: str | None = None,
) -> bool:
    """Append one row. Returns whether it was written. Never raises."""
    try:
        name = actor
        if not name and actor_user_id:
            user = await db_client.get_user_by_id(int(actor_user_id))
            name = (
                (
                    getattr(user, "name", None)
                    or getattr(user, "email", None)
                    or f"Member {actor_user_id}"
                )
                if user
                else f"Member {actor_user_id}"
            )
        async with db_client.async_session() as session:
            session.add(
                AuditEntryModel(
                    organization_id=organization_id,
                    actor_user_id=actor_user_id,
                    actor=(name or "Decibyl")[:120],
                    action=action[:48],
                    subject_kind=subject_kind[:32],
                    subject_id=None if subject_id is None else str(subject_id)[:64],
                    subject=(subject or "")[:255] or None,
                    before=_brief(before),
                    after=_brief(after),
                    note=(note or "")[:500] or None,
                )
            )
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - the act already happened
        logger.warning(
            "Could not write audit row {} for org {}: {}", action, organization_id, exc
        )
        return False


async def rows(
    organization_id: int,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 5_000,
) -> list[AuditEntryModel]:
    """Newest first, inside the window."""
    async with db_client.async_session() as session:
        query = select(AuditEntryModel).where(
            AuditEntryModel.organization_id == organization_id
        )
        if since is not None:
            query = query.where(AuditEntryModel.at >= since)
        if until is not None:
            query = query.where(AuditEntryModel.at < until)
        result = await session.execute(
            query.order_by(AuditEntryModel.at.desc(), AuditEntryModel.id.desc()).limit(
                limit
            )
        )
        return list(result.scalars().all())


def as_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "at": row.at.isoformat() if row.at else None,
        "actor": row.actor,
        "actor_user_id": row.actor_user_id,
        "action": row.action,
        "subject_kind": row.subject_kind,
        "subject_id": row.subject_id,
        "subject": row.subject,
        "before": row.before,
        "after": row.after,
        "note": row.note,
    }


def as_csv(entries: list[Any]) -> str:
    import json

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in entries:
        d = as_dict(row)
        writer.writerow(
            [
                d["at"],
                d["actor"],
                d["actor_user_id"],
                d["action"],
                d["subject_kind"],
                d["subject_id"],
                d["subject"],
                json.dumps(d["before"]) if d["before"] is not None else "",
                json.dumps(d["after"]) if d["after"] is not None else "",
                d["note"] or "",
            ]
        )
    return buffer.getvalue()
