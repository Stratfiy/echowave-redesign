"""One reading of the two staff audit logs (ADMIN-2, A6).

``admin_action_log`` records sensitive staff acts (impersonation, invites,
trial changes); ``billing_audit_log`` records money (rates, credit). Both were
write-only: every row was durable and nobody could read one without SQL.

Read here as one stream, newest first, through ``UNION ALL`` so the database
does the merge and the ordering. Pagination is by cursor rather than offset:
an audit log grows while it is being read, and an offset page would repeat or
skip rows as new ones arrive at the top.

The cursor is ``<created_at ISO>~<source>~<id>``: ``created_at`` alone is not
unique (a trial change and its audit row can share a timestamp), and ids are
only unique within one table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Integer,
    String,
    cast,
    literal,
    null,
    select,
    tuple_,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    AdminActionLogModel,
    BillingAuditLogModel,
    OrganizationModel,
    UserModel,
)

ADMIN = "admin"
BILLING = "billing"

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

CURSOR_SEPARATOR = "~"


@dataclass(frozen=True)
class Cursor:
    created_at: datetime
    source: str | None = None
    id: int | None = None

    def encode(self) -> str:
        return CURSOR_SEPARATOR.join(
            [self.created_at.isoformat(), self.source or "", str(self.id or "")]
        )


def parse_cursor(raw: str | None) -> Cursor | None:
    """A cursor from a previous page, or a bare ISO timestamp.

    Raises ``ValueError`` on anything else, so the route can answer 400
    rather than silently returning the first page.
    """
    if raw is None or not raw.strip():
        return None
    parts = raw.strip().split(CURSOR_SEPARATOR)
    when = datetime.fromisoformat(parts[0])
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    if len(parts) == 1:
        return Cursor(created_at=when)
    if len(parts) != 3 or parts[1] not in (ADMIN, BILLING):
        raise ValueError("Malformed cursor")
    return Cursor(created_at=when, source=parts[1], id=int(parts[2]))


def _admin_rows():
    a = AdminActionLogModel
    return select(
        literal(ADMIN, String).label("source"),
        a.id.label("id"),
        a.created_at.label("created_at"),
        a.actor_user_id.label("actor_user_id"),
        a.action.label("action"),
        a.target_organization_id.label("organization_id"),
        a.target_user_id.label("target_user_id"),
        cast(a.note, String).label("note"),
        cast(null(), JSON).label("old_value"),
        cast(null(), JSON).label("new_value"),
    )


def _billing_rows():
    b = BillingAuditLogModel
    return select(
        literal(BILLING, String).label("source"),
        b.id.label("id"),
        b.created_at.label("created_at"),
        b.actor_user_id.label("actor_user_id"),
        b.action.label("action"),
        b.organization_id.label("organization_id"),
        # Billing rows name an account, never a person.
        cast(null(), Integer).label("target_user_id"),
        cast(b.note, String).label("note"),
        b.old_value.label("old_value"),
        b.new_value.label("new_value"),
    )


async def audit_page(
    session: AsyncSession,
    *,
    organization_id: int | None = None,
    actor_user_id: int | None = None,
    action: str | None = None,
    before: Cursor | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """One page of both logs, newest first, filtered at the query level."""
    limit = max(1, min(int(limit), MAX_LIMIT))

    admin = _admin_rows()
    billing = _billing_rows()
    if organization_id is not None:
        admin = admin.where(
            AdminActionLogModel.target_organization_id == organization_id
        )
        billing = billing.where(BillingAuditLogModel.organization_id == organization_id)
    if actor_user_id is not None:
        admin = admin.where(AdminActionLogModel.actor_user_id == actor_user_id)
        billing = billing.where(BillingAuditLogModel.actor_user_id == actor_user_id)
    if action:
        admin = admin.where(AdminActionLogModel.action == action)
        billing = billing.where(BillingAuditLogModel.action == action)

    log = union_all(admin, billing).subquery("audit")
    actor = UserModel.__table__.alias("actor")
    target = UserModel.__table__.alias("target")
    org = OrganizationModel.__table__.alias("org")

    query = (
        select(
            log,
            actor.c.email.label("actor_email"),
            target.c.email.label("target_user_email"),
            org.c.billing_name.label("organization_billing_name"),
            org.c.name.label("organization_name"),
        )
        .outerjoin(actor, actor.c.id == log.c.actor_user_id)
        .outerjoin(target, target.c.id == log.c.target_user_id)
        .outerjoin(org, org.c.id == log.c.organization_id)
    )
    if before is not None:
        if before.source is None:
            query = query.where(log.c.created_at < before.created_at)
        else:
            query = query.where(
                tuple_(log.c.created_at, log.c.source, log.c.id)
                < tuple_(
                    literal(before.created_at),
                    literal(before.source, String),
                    literal(before.id),
                )
            )
    query = query.order_by(
        log.c.created_at.desc().nulls_last(),
        log.c.source.desc(),
        log.c.id.desc(),
    ).limit(limit + 1)

    rows = (await session.execute(query)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    entries = [
        {
            "key": f"{r.source}:{r.id}",
            "source": r.source,
            "id": int(r.id),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "actor_user_id": r.actor_user_id,
            "actor_email": r.actor_email,
            "action": r.action,
            "organization_id": r.organization_id,
            "organization_name": r.organization_billing_name or r.organization_name,
            "target_user_id": r.target_user_id,
            "target_user_email": r.target_user_email,
            "note": r.note,
            "old_value": r.old_value,
            "new_value": r.new_value,
        }
        for r in rows
    ]
    next_before = None
    if has_more and rows and rows[-1].created_at is not None:
        last = rows[-1]
        next_before = Cursor(
            created_at=last.created_at, source=last.source, id=int(last.id)
        ).encode()
    return {"entries": entries, "next_before": next_before, "limit": limit}


async def known_actions(session: AsyncSession) -> list[str]:
    """Every action either log has recorded, for the filter's choices.

    Read from the data rather than a list in code, so an action somebody adds
    next month is filterable the day its first row is written.
    """
    rows = await session.execute(
        union_all(
            select(AdminActionLogModel.action).distinct(),
            select(BillingAuditLogModel.action).distinct(),
        )
    )
    return sorted({str(a) for (a,) in rows.all() if a})
