"""Support tickets: the customer's side (screen 28) and staff's (screen 32).

Scope, the two ways it matters here:

* **Customer reads and writes** name the person and their selected
  workspace, in the query: a ticket is its requester's, in the workspace it
  was opened from. A colleague in the same workspace, or the same person in
  another workspace, gets "not found", never someone else's ticket.
* **Staff reads and writes** are role-gated at the route (any staff tier)
  and every one that touches a case writes ``admin_action_log``: who looked
  at which customer's case and when, who replied, who noted, who changed
  what. Staff see the ticket, the customer thread, their notes, and exactly
  what the customer chose to share -- not the customer's history.

Internal notes live in ``support_notes``. No function on the customer side
selects from that table, which is what keeps a note out of a customer
response, rather than a filter somebody could forget.

Repeats are harmless: a submit carries an idempotency key (one ticket per
key per person) and a reply a client key (one message per key per ticket),
so a double tap, a retry after a timeout or a second tab never sends twice.
Assignment and status changes name the ticket ``version`` they read; a
stale one is refused with the current state (two staff picking up one case).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.models import AdminActionLogModel, OrganizationModel, UserModel
from api.db.support_models import (
    SupportActionModel,
    SupportAttachmentModel,
    SupportMessageModel,
    SupportNoteModel,
    SupportTicketModel,
)
from api.services import events
from api.services.support import sharing

OPEN = "open"
WAITING = "waiting_on_customer"
IN_PROGRESS = "in_progress"
RESOLVED = "resolved"
STATUSES = (OPEN, WAITING, IN_PROGRESS, RESOLVED)
ACTIVE = (OPEN, WAITING, IN_PROGRESS)

SEVERITIES = ("low", "normal", "high", "urgent")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}

#: What a person can say the ticket is about. "other" catches the rest, so
#: nothing a person needs is missing from the list.
CATEGORIES: dict[str, str] = {
    "something_failed": "Something didn't work",
    "account": "My account or signing in",
    "connections": "A connected app",
    "usage_limits": "Daily limits",
    "privacy": "My data and privacy",
    "other": "Something else",
}

#: First-response targets by severity, for "overdue" in the queue. Internal
#: operating targets, not a promise to customers; placeholders until the
#: founder sets support hours (SUPPORT.md).
FIRST_RESPONSE_TARGET = {
    "urgent": timedelta(hours=1),
    "high": timedelta(hours=4),
    "normal": timedelta(hours=24),
    "low": timedelta(hours=72),
}

BODY_LIMIT = 8000
#: A staff member opening the same case again within this window is one
#: audit row, not one per page refresh.
VIEW_AUDIT_WINDOW = timedelta(minutes=30)


class TicketError(ValueError):
    pass


class NotFound(TicketError):
    pass


class Stale(TicketError):
    def __init__(self, current: dict[str, Any]):
        self.current = current
        super().__init__("Someone changed this case since you opened it.")


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _clean_body(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise TicketError("Write something first.")
    if len(body) > BODY_LIMIT:
        raise TicketError(f"Keep it under {BODY_LIMIT} characters.")
    return body


def _key(value: str | None) -> str | None:
    value = (value or "").strip()[:64]
    return value or None


def overdue(ticket: SupportTicketModel, now: datetime | None = None) -> bool:
    if ticket.status == RESOLVED or ticket.first_response_at is not None:
        return False
    now = now or datetime.now(UTC)
    target = FIRST_RESPONSE_TARGET.get(ticket.severity, FIRST_RESPONSE_TARGET["normal"])
    return now - ticket.created_at > target


def _ticket_dict(t: SupportTicketModel, *, staff: bool = False) -> dict[str, Any]:
    row = {
        "id": t.id,
        "category": t.category,
        "category_label": CATEGORIES.get(t.category, t.category),
        "subject": t.subject,
        "status": t.status,
        "affected": (
            {"kind": t.affected_kind, "id": t.affected_id} if t.affected_kind else None
        ),
        "reopened_count": t.reopened_count,
        "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
        "resolved_at": _iso(t.resolved_at),
    }
    if staff:
        row.update(
            {
                "organization_id": t.organization_id,
                "requester_user_id": t.requester_user_id,
                "severity": t.severity,
                "assignee_user_id": t.assignee_user_id,
                "linked_incident": t.linked_incident,
                "version": t.version,
                "first_response_at": _iso(t.first_response_at),
                "overdue": overdue(t),
            }
        )
    return row


def _message_dict(m: SupportMessageModel) -> dict[str, Any]:
    return {
        "id": m.id,
        "author_kind": m.author_kind,
        "body": m.body,
        "created_at": _iso(m.created_at),
    }


def _attachment_dict(a: SupportAttachmentModel) -> dict[str, Any]:
    return {
        "id": a.id,
        "file_name": a.file_name,
        "content_type": a.content_type,
        "size_bytes": a.size_bytes,
        "created_at": _iso(a.created_at),
    }


def _audit(
    session: Any,
    *,
    actor: int,
    action: str,
    ticket: SupportTicketModel,
    note: str = "",
) -> None:
    session.add(
        AdminActionLogModel(
            actor_user_id=actor,
            action=action,
            target_user_id=ticket.requester_user_id,
            target_organization_id=ticket.organization_id,
            note=f"ticket={ticket.id}; {note}"[:500],
        )
    )


# --- the customer's side (screen 28) ----------------------------------------


async def create(
    *,
    organization_id: int,
    user_id: int,
    category: str,
    description: str,
    subject: str | None = None,
    affected_kind: str | None = None,
    affected_id: int | None = None,
    share: list[str] | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Open a ticket. Returns (ticket, created). The same key again returns
    the first ticket and False -- never a second one."""
    if category not in CATEGORIES:
        raise TicketError("Choose what this is about.")
    description = _clean_body(description)
    key = _key(idempotency_key)
    if key:
        existing = await _by_key(user_id, key)
        if existing is not None:
            return _ticket_dict(existing), False
    preview = await sharing.build(
        organization_id=organization_id,
        user_id=user_id,
        affected_kind=affected_kind,
        affected_id=affected_id,
        share=share,
    )
    title = (subject or "").strip() or description.splitlines()[0]
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        inserted = (
            await session.execute(
                insert(SupportTicketModel)
                .values(
                    organization_id=organization_id,
                    requester_user_id=user_id,
                    category=category,
                    subject=title[:200],
                    status=OPEN,
                    severity="normal",
                    affected_kind=affected_kind,
                    affected_id=affected_id,
                    shared=sharing.snapshot(preview),
                    idempotency_key=key,
                    version=1,
                    reopened_count=0,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(constraint="ux_support_tickets_requester_key")
                .returning(SupportTicketModel.id)
            )
        ).first()
        if inserted is None:
            # The same key won a race a moment ago.
            await session.rollback()
            existing = await _by_key(user_id, key or "")
            assert existing is not None
            return _ticket_dict(existing), False
        ticket_id = inserted[0]
        session.add(
            SupportMessageModel(
                ticket_id=ticket_id,
                organization_id=organization_id,
                author_user_id=user_id,
                author_kind="customer",
                body=description,
                created_at=now,
            )
        )
        await events.emit(
            "ticket_created",
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            properties={"status": OPEN, "reason_code": category},
        )
        await session.commit()
        ticket = await session.get(SupportTicketModel, ticket_id)
        return _ticket_dict(ticket), True


async def _by_key(user_id: int, key: str) -> SupportTicketModel | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(SupportTicketModel).where(
                SupportTicketModel.requester_user_id == user_id,
                SupportTicketModel.idempotency_key == key,
            )
        )


async def _mine(
    session: Any, organization_id: int, user_id: int, ticket_id: int
) -> SupportTicketModel:
    ticket = await session.scalar(
        select(SupportTicketModel).where(
            SupportTicketModel.id == ticket_id,
            SupportTicketModel.organization_id == organization_id,
            SupportTicketModel.requester_user_id == user_id,
        )
    )
    if ticket is None:
        raise NotFound("That request is not here.")
    return ticket


async def list_mine(organization_id: int, user_id: int) -> list[dict[str, Any]]:
    async with db_client.async_session() as session:
        tickets = (
            (
                await session.execute(
                    select(SupportTicketModel)
                    .where(
                        SupportTicketModel.organization_id == organization_id,
                        SupportTicketModel.requester_user_id == user_id,
                    )
                    .order_by(SupportTicketModel.updated_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        last_author = await _last_authors(session, [t.id for t in tickets])
    out = []
    for t in tickets:
        row = _ticket_dict(t)
        row["support_replied"] = last_author.get(t.id) in ("staff", "system")
        out.append(row)
    return out


async def _last_authors(session: Any, ticket_ids: list[int]) -> dict[int, str]:
    if not ticket_ids:
        return {}
    latest = (
        select(
            SupportMessageModel.ticket_id,
            func.max(SupportMessageModel.id).label("last_id"),
        )
        .where(SupportMessageModel.ticket_id.in_(ticket_ids))
        .group_by(SupportMessageModel.ticket_id)
        .subquery()
    )
    rows = await session.execute(
        select(SupportMessageModel.ticket_id, SupportMessageModel.author_kind).join(
            latest, SupportMessageModel.id == latest.c.last_id
        )
    )
    return {ticket_id: kind for ticket_id, kind in rows.all()}


async def get_mine(
    organization_id: int, user_id: int, ticket_id: int
) -> dict[str, Any]:
    """The customer's ticket: header, thread, files, what was shared and
    the status of any staff action -- and nothing from ``support_notes``."""
    async with db_client.async_session() as session:
        ticket = await _mine(session, organization_id, user_id, ticket_id)
        return await _customer_view(session, ticket)


async def _customer_view(session: Any, ticket: SupportTicketModel) -> dict[str, Any]:
    messages = (
        (
            await session.execute(
                select(SupportMessageModel)
                .where(SupportMessageModel.ticket_id == ticket.id)
                .order_by(SupportMessageModel.created_at, SupportMessageModel.id)
            )
        )
        .scalars()
        .all()
    )
    files = (
        (
            await session.execute(
                select(SupportAttachmentModel)
                .where(SupportAttachmentModel.ticket_id == ticket.id)
                .order_by(SupportAttachmentModel.id)
            )
        )
        .scalars()
        .all()
    )
    actions = (
        (
            await session.execute(
                select(SupportActionModel)
                .where(SupportActionModel.ticket_id == ticket.id)
                .order_by(SupportActionModel.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        **_ticket_dict(ticket),
        "shared": ticket.shared,
        "messages": [_message_dict(m) for m in messages],
        "attachments": [_attachment_dict(a) for a in files],
        # What the customer may know about an action: what it does and where
        # it stands. Not who asked, who approved, or why.
        "actions": [
            {
                "id": a.id,
                "summary": (a.preview or {}).get("customer_summary")
                or (a.preview or {}).get("title"),
                "state": a.state,
                "finished_at": _iso(a.finished_at),
            }
            for a in actions
        ],
    }


async def reply_as_customer(
    *,
    organization_id: int,
    user_id: int,
    ticket_id: int,
    body: str,
    client_key: str | None,
) -> dict[str, Any]:
    body = _clean_body(body)
    key = _key(client_key)
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        ticket = await _mine(session, organization_id, user_id, ticket_id)
        if ticket.status == RESOLVED:
            raise TicketError("This request is resolved. Reopen it to reply.")
        message = await _add_message(
            session,
            ticket=ticket,
            author_user_id=user_id,
            author_kind="customer",
            body=body,
            client_key=key,
            now=now,
        )
        if message["created"] and ticket.status == WAITING:
            await _set_status(session, ticket, IN_PROGRESS, now)
        await session.commit()
        return message["message"]


async def _add_message(
    session: Any,
    *,
    ticket: SupportTicketModel,
    author_user_id: int | None,
    author_kind: str,
    body: str,
    client_key: str | None,
    now: datetime,
) -> dict[str, Any]:
    """One message; the same client key again is the first message."""
    if client_key:
        existing = await session.scalar(
            select(SupportMessageModel).where(
                SupportMessageModel.ticket_id == ticket.id,
                SupportMessageModel.client_key == client_key,
            )
        )
        if existing is not None:
            return {"message": _message_dict(existing), "created": False}
    inserted = (
        await session.execute(
            insert(SupportMessageModel)
            .values(
                ticket_id=ticket.id,
                organization_id=ticket.organization_id,
                author_user_id=author_user_id,
                author_kind=author_kind,
                body=body,
                client_key=client_key,
                created_at=now,
            )
            .on_conflict_do_nothing(constraint="ux_support_messages_key")
            .returning(SupportMessageModel.id)
        )
    ).first()
    if inserted is None:
        existing = await session.scalar(
            select(SupportMessageModel).where(
                SupportMessageModel.ticket_id == ticket.id,
                SupportMessageModel.client_key == client_key,
            )
        )
        return {"message": _message_dict(existing), "created": False}
    message = await session.get(SupportMessageModel, inserted[0])
    ticket.updated_at = now
    return {"message": _message_dict(message), "created": True}


async def _set_status(
    session: Any, ticket: SupportTicketModel, status: str, now: datetime
) -> None:
    ticket.status = status
    ticket.updated_at = now
    ticket.version = (ticket.version or 1) + 1
    if status == RESOLVED:
        ticket.resolved_at = now


async def resolve_as_customer(
    *, organization_id: int, user_id: int, ticket_id: int
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        ticket = await _mine(session, organization_id, user_id, ticket_id)
        if ticket.status != RESOLVED:
            await _set_status(session, ticket, RESOLVED, now)
            await _add_message(
                session,
                ticket=ticket,
                author_user_id=user_id,
                author_kind="system",
                body="Marked resolved by you.",
                client_key=None,
                now=now,
            )
            await events.emit(
                "ticket_resolved",
                session=session,
                user_id=user_id,
                organization_id=organization_id,
                properties={"status": RESOLVED, "reason_code": "by_customer"},
            )
        await session.commit()
        await session.refresh(ticket)
        return await _customer_view(session, ticket)


async def reopen_as_customer(
    *, organization_id: int, user_id: int, ticket_id: int, body: str | None = None
) -> dict[str, Any]:
    """Reopen a resolved ticket. History, shares and action evidence stay
    exactly as they were; the reopen is one more line in the thread."""
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        ticket = await _mine(session, organization_id, user_id, ticket_id)
        if ticket.status != RESOLVED:
            raise TicketError("This request is still open.")
        await _set_status(session, ticket, OPEN, now)
        ticket.resolved_at = None
        ticket.reopened_count = (ticket.reopened_count or 0) + 1
        await _add_message(
            session,
            ticket=ticket,
            author_user_id=user_id,
            author_kind="customer",
            body=_clean_body(body) if body and body.strip() else "Reopened.",
            client_key=None,
            now=now,
        )
        await events.emit(
            "ticket_reopened",
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            properties={"status": OPEN},
        )
        await session.commit()
        await session.refresh(ticket)
        return await _customer_view(session, ticket)


# --- staff (screen 32) -------------------------------------------------------


async def queue(
    *,
    staff_id: int,
    status: str | None = "active",
    assignee: str | None = None,
    severity: str | None = None,
    overdue_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """The inbox. Oldest first and stable, so a ticket being worked does not
    jump under the cursor when another one changes."""
    query = (
        select(SupportTicketModel, UserModel.email, OrganizationModel.name)
        .join(UserModel, UserModel.id == SupportTicketModel.requester_user_id)
        .join(
            OrganizationModel,
            OrganizationModel.id == SupportTicketModel.organization_id,
        )
    )
    if status == "active":
        query = query.where(SupportTicketModel.status.in_(ACTIVE))
    elif status:
        if status not in STATUSES:
            raise TicketError("Unknown status filter.")
        query = query.where(SupportTicketModel.status == status)
    if assignee == "me":
        query = query.where(SupportTicketModel.assignee_user_id == staff_id)
    elif assignee == "unassigned":
        query = query.where(SupportTicketModel.assignee_user_id.is_(None))
    elif assignee:
        query = query.where(SupportTicketModel.assignee_user_id == int(assignee))
    if severity:
        if severity not in SEVERITIES:
            raise TicketError("Unknown severity filter.")
        query = query.where(SupportTicketModel.severity == severity)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                query.order_by(
                    SupportTicketModel.created_at, SupportTicketModel.id
                ).limit(min(max(limit, 1), 200))
            )
        ).all()
        last_author = await _last_authors(session, [t.id for t, _, _ in rows])
    out = []
    for ticket, email, org_name in rows:
        row = _ticket_dict(ticket, staff=True)
        if overdue_only and not row["overdue"]:
            continue
        row["requester_email"] = email
        row["workspace_name"] = org_name
        row["next_step"] = _next_step(ticket, last_author.get(ticket.id))
        out.append(row)
    return out


def _next_step(ticket: SupportTicketModel, last_author: str | None) -> str:
    if ticket.status == RESOLVED:
        return "None"
    if ticket.assignee_user_id is None:
        return "Assign"
    if ticket.status == WAITING:
        return "Waiting on customer"
    if last_author == "customer":
        return "Reply"
    return "Follow up"


async def _staff_ticket(session: Any, ticket_id: int) -> SupportTicketModel:
    ticket = await session.get(SupportTicketModel, ticket_id)
    if ticket is None:
        raise NotFound("No such ticket.")
    return ticket


async def case(*, ticket_id: int, staff_id: int) -> dict[str, Any]:
    """Everything staff may see about one case, and an audit row saying
    they looked (at most one per person per case per half hour)."""
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        ticket = await _staff_ticket(session, ticket_id)
        recent = await session.scalar(
            select(AdminActionLogModel.id).where(
                AdminActionLogModel.actor_user_id == staff_id,
                AdminActionLogModel.action == "support_case_viewed",
                AdminActionLogModel.note.like(f"ticket={ticket.id};%"),
                AdminActionLogModel.created_at > now - VIEW_AUDIT_WINDOW,
            )
        )
        if recent is None:
            _audit(session, actor=staff_id, action="support_case_viewed", ticket=ticket)
            await session.commit()
            await session.refresh(ticket)
        view = await _customer_view(session, ticket)
        notes = (
            (
                await session.execute(
                    select(SupportNoteModel)
                    .where(SupportNoteModel.ticket_id == ticket.id)
                    .order_by(SupportNoteModel.created_at, SupportNoteModel.id)
                )
            )
            .scalars()
            .all()
        )
        requester = await session.get(UserModel, ticket.requester_user_id)
        org = await session.get(OrganizationModel, ticket.organization_id)
        history = (
            (
                await session.execute(
                    select(AdminActionLogModel)
                    .where(AdminActionLogModel.note.like(f"ticket={ticket.id};%"))
                    .order_by(AdminActionLogModel.created_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        actor_ids = {h.actor_user_id for h in history} | {
            n.author_user_id for n in notes if n.author_user_id
        }
        names = await _emails(session, actor_ids)
    view.pop("actions", None)
    return {
        **view,
        **_ticket_dict(ticket, staff=True),
        "requester": {
            "id": ticket.requester_user_id,
            "email": getattr(requester, "email", None),
        },
        "workspace": {"id": ticket.organization_id, "name": getattr(org, "name", None)},
        "notes": [
            {
                "id": n.id,
                "author": names.get(n.author_user_id) or f"Staff #{n.author_user_id}",
                "body": n.body,
                "created_at": _iso(n.created_at),
            }
            for n in notes
        ],
        "history": [
            {
                "id": h.id,
                "at": _iso(h.created_at),
                "actor": names.get(h.actor_user_id) or f"Staff #{h.actor_user_id}",
                "action": h.action,
                "note": (h.note or "").split("; ", 1)[1]
                if "; " in (h.note or "")
                else "",
            }
            for h in history
        ],
        "diagnostics": await diagnostics(ticket),
    }


async def _emails(session: Any, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await session.execute(
        select(UserModel.id, UserModel.email).where(UserModel.id.in_(ids))
    )
    return {uid: email for uid, email in rows.all() if email}


async def diagnostics(ticket: SupportTicketModel) -> dict[str, Any]:
    """Read-only, live, and only as wide as the share: the affected task's
    state and moves (never its words) when the customer shared its details;
    the person's daily allowances when quotas are on."""
    from api.services import quotas
    from api.services.workflow import task_ledger

    out: dict[str, Any] = {"task": None, "allowances": None}
    shared_keys = {s.get("key") for s in (ticket.shared or {}).get("sections", [])}
    if ticket.affected_kind == "task" and sharing.TASK_METADATA in shared_keys:
        task = await db_client.get_task(
            ticket.affected_id, organization_id=ticket.organization_id
        )
        if task is None:
            out["task"] = {"state": "missing", "note": "The task no longer exists."}
        else:
            out["task"] = {
                "id": task.id,
                "state": task_ledger.state_of(task),
                "version": int(task.state_version or 0),
                "history": await task_ledger.history(ticket.organization_id, task.id),
            }
    if quotas.enabled():
        out["allowances"] = await quotas.status(ticket.requester_user_id)
    return out


async def reply_as_staff(
    *,
    ticket_id: int,
    staff_id: int,
    body: str,
    client_key: str | None,
    then_status: str | None = WAITING,
) -> dict[str, Any]:
    """A reply the customer reads. Retried with the same key, still one."""
    body = _clean_body(body)
    if then_status is not None and then_status not in STATUSES:
        raise TicketError("Unknown status.")
    key = _key(client_key)
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        ticket = await _staff_ticket(session, ticket_id)
        result = await _add_message(
            session,
            ticket=ticket,
            author_user_id=staff_id,
            author_kind="staff",
            body=body,
            client_key=key,
            now=now,
        )
        if result["created"]:
            if ticket.first_response_at is None:
                ticket.first_response_at = now
            if then_status and then_status != ticket.status:
                was_resolved = ticket.status == RESOLVED
                await _set_status(session, ticket, then_status, now)
                if then_status == RESOLVED:
                    await events.emit(
                        "ticket_resolved",
                        session=session,
                        user_id=ticket.requester_user_id,
                        organization_id=ticket.organization_id,
                        properties={"status": RESOLVED, "reason_code": "by_staff"},
                    )
                elif was_resolved:
                    ticket.resolved_at = None
            _audit(
                session,
                actor=staff_id,
                action="support_reply_sent",
                ticket=ticket,
                note=f"message={result['message']['id']}",
            )
        await session.commit()
        return result["message"]


async def add_note(
    *, ticket_id: int, staff_id: int, body: str, client_key: str | None
) -> dict[str, Any]:
    body = _clean_body(body)
    key = _key(client_key)
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        ticket = await _staff_ticket(session, ticket_id)
        if key:
            existing = await session.scalar(
                select(SupportNoteModel).where(
                    SupportNoteModel.ticket_id == ticket.id,
                    SupportNoteModel.client_key == key,
                )
            )
            if existing is not None:
                return {
                    "id": existing.id,
                    "body": existing.body,
                    "created_at": _iso(existing.created_at),
                }
        note = SupportNoteModel(
            ticket_id=ticket.id,
            author_user_id=staff_id,
            body=body,
            client_key=key,
            created_at=now,
        )
        session.add(note)
        await session.flush()
        _audit(
            session,
            actor=staff_id,
            action="support_note_added",
            ticket=ticket,
            note=f"note={note.id}",
        )
        saved = {"id": note.id, "body": note.body, "created_at": _iso(note.created_at)}
        await session.commit()
        return saved


async def update_case(
    *,
    ticket_id: int,
    staff_id: int,
    expected_version: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Assign, set severity or status, link an incident -- only if the case
    is still at ``expected_version``. Raises Stale with the current case."""
    allowed = {"assignee_user_id", "severity", "status", "linked_incident"}
    unknown = set(changes) - allowed
    if unknown:
        raise TicketError(f"{min(unknown)} cannot be changed here.")
    if "severity" in changes and changes["severity"] not in SEVERITIES:
        raise TicketError("Unknown severity.")
    if "status" in changes and changes["status"] not in STATUSES:
        raise TicketError("Unknown status.")
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        ticket = await _staff_ticket(session, ticket_id)
        if changes.get("assignee_user_id") is not None:
            assignee = await session.get(UserModel, int(changes["assignee_user_id"]))
            if assignee is None or not assignee.staff_role:
                raise TicketError("Cases are assigned to staff.")
        if "linked_incident" in changes:
            value = (changes["linked_incident"] or "").strip()[:64]
            changes["linked_incident"] = value or None
        values: dict[str, Any] = {
            **changes,
            "version": SupportTicketModel.version + 1,
            "updated_at": now,
        }
        before_status = ticket.status
        if changes.get("status") == RESOLVED and before_status != RESOLVED:
            values["resolved_at"] = now
        elif "status" in changes and changes["status"] != RESOLVED:
            values["resolved_at"] = None
        hit = (
            await session.execute(
                update(SupportTicketModel)
                .where(
                    SupportTicketModel.id == ticket.id,
                    SupportTicketModel.version == expected_version,
                )
                .values(**values)
                .returning(SupportTicketModel.id)
            )
        ).first()
        if hit is None:
            await session.rollback()
            fresh = await _staff_ticket(session, ticket_id)
            raise Stale(_ticket_dict(fresh, staff=True))
        _audit(
            session,
            actor=staff_id,
            action="support_case_updated",
            ticket=ticket,
            note="; ".join(f"{k}={v}" for k, v in sorted(changes.items())),
        )
        if changes.get("status") == RESOLVED and before_status != RESOLVED:
            await events.emit(
                "ticket_resolved",
                session=session,
                user_id=ticket.requester_user_id,
                organization_id=ticket.organization_id,
                properties={"status": RESOLVED, "reason_code": "by_staff"},
            )
        await session.commit()
        fresh = (
            await session.execute(
                select(SupportTicketModel)
                .where(SupportTicketModel.id == ticket_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return _ticket_dict(fresh, staff=True)


async def summary(days: int = 30) -> dict[str, Any]:
    """First response, resolution and reopening -- each measured on its own
    (handoff 33). Satisfaction is not collected yet, and says so."""
    since = datetime.now(UTC) - timedelta(days=days)
    async with db_client.async_session() as session:
        tickets = (
            (
                await session.execute(
                    select(SupportTicketModel).where(
                        SupportTicketModel.created_at >= since
                    )
                )
            )
            .scalars()
            .all()
        )
        open_now = await session.scalar(
            select(func.count(SupportTicketModel.id)).where(
                or_(*[SupportTicketModel.status == s for s in ACTIVE])
            )
        )
    first = [
        (t.first_response_at - t.created_at).total_seconds() / 60
        for t in tickets
        if t.first_response_at
    ]
    resolved = [
        (t.resolved_at - t.created_at).total_seconds() / 60
        for t in tickets
        if t.resolved_at
    ]
    return {
        "window_days": days,
        "tickets": len(tickets),
        "open_now": int(open_now or 0),
        "first_response_minutes_median": round(median(first), 1) if first else None,
        "first_response_measured": len(first),
        "resolution_minutes_median": round(median(resolved), 1) if resolved else None,
        "resolved": len(resolved),
        "reopened": sum(1 for t in tickets if (t.reopened_count or 0) > 0),
        "satisfaction": None,
        "satisfaction_note": "Not collected yet.",
    }
