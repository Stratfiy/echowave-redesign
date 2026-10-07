"""A support action's lifecycle (handoff 33 "Request and approval lifecycle").

Record request -> verify identity and scope -> preview exact action ->
obtain required approval -> execute -> reconcile result -> notify customer.

* **Request** (any staff): a typed command (``commands.py``) against one
  workspace and person, with a reason. Scope is verified here -- the person
  is a member of the workspace; the ticket, if any, is from that workspace;
  a command that changes the customer's own things needs their ticket. The
  preview (target, environment, old and new values, impact, dependencies,
  approvers) is stored, and ``version`` binds it. The same idempotency key
  again is the same action: repeat clicks make one command.
* **Approve** (a second person, at least the command's approver tier):
  names the version they read. The requester can never approve -- the
  service refuses and a check constraint in the database refuses too. If
  the target moved since the preview, the approval is refused: the
  requester previews again, which is a new version.
* **Revise**: new parameters are a new version; any approval is cleared.
* **Run**: compare-and-swap from approved to queued, then the ARQ worker
  claims queued -> running and executes once. Accepted is queued, never
  succeeded. A repeat click while it is in flight changes nothing.
* **Outcome unknown**: an unexpected error mid-run, or a run whose worker
  died (swept after ``STALE_RUNNING``), is ``outcome_unknown`` -- never
  failed, never retried blind. ``reconcile`` asks the command whether the
  change is there and settles it.
* **Expiry**: a request lapses after 24 hours unapproved, an approval after
  15 minutes unrun (handoff 9).
* **Notify**: when an action on a ticket finishes, the customer's thread
  gets one line saying what was done (or that it was not).

Every step writes ``admin_action_log`` (actor, ticket, target, reason,
parameters, approval, outcome) and the catalogue's ``support_action_*``
events. A support request does not approve sending, booking or spending:
none of the commands sends, books or spends on the customer's behalf.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from api import constants
from api.db import db_client
from api.db.models import (
    AdminActionLogModel,
    OrganizationMembershipModel,
    UserModel,
)
from api.db.support_models import (
    SupportActionModel,
    SupportMessageModel,
    SupportTicketModel,
)
from api.enums import STAFF_ROLE_RANK
from api.services import events
from api.services.support import commands

REQUESTED = "requested"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
CANCELLED = "cancelled"
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
OUTCOME_UNKNOWN = "outcome_unknown"
STATES = (
    REQUESTED,
    APPROVED,
    REJECTED,
    EXPIRED,
    CANCELLED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    FAILED,
    OUTCOME_UNKNOWN,
)
FINISHED = frozenset({REJECTED, EXPIRED, CANCELLED, SUCCEEDED, FAILED})

REQUEST_TTL = timedelta(hours=24)
APPROVAL_TTL = timedelta(minutes=15)
#: A run claimed this long ago and not finished is outcome unknown.
STALE_RUNNING = timedelta(minutes=10)

UNKNOWN_COPY = (
    "We are checking whether this happened. Do not request it again; "
    "reconcile it first."
)


class ActionError(ValueError):
    pass


class NotFound(ActionError):
    pass


class Forbidden(ActionError):
    pass


class Changed(ActionError):
    """The version named is not the action's current one, or the target
    moved since the preview."""


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _rank(user: Any) -> int:
    return STAFF_ROLE_RANK.get(getattr(user, "staff_role", None) or "", -1)


def as_dict(
    a: SupportActionModel, names: dict[int, str] | None = None
) -> dict[str, Any]:
    names = names or {}

    def who(uid: int | None) -> str | None:
        if uid is None:
            return None
        return names.get(uid) or f"Staff #{uid}"

    return {
        "id": a.id,
        "ticket_id": a.ticket_id,
        "organization_id": a.organization_id,
        "target_user_id": a.target_user_id,
        "kind": a.kind,
        "title": commands.COMMANDS[a.kind].title
        if a.kind in commands.COMMANDS
        else a.kind,
        "params": a.params,
        "preview": {k: v for k, v in (a.preview or {}).items() if k != "captured"},
        "version": a.version,
        "reason": a.reason,
        "state": a.state,
        "environment": a.environment,
        "approver_role": (
            commands.COMMANDS[a.kind].approver_role
            if a.kind in commands.COMMANDS
            else None
        ),
        "requested_by": who(a.requested_by),
        "requested_by_id": a.requested_by,
        "approved_by": who(a.approved_by),
        "approved_version": a.approved_version,
        "approved_at": _iso(a.approved_at),
        "decided_note": a.decided_note,
        "run_by": who(a.run_by),
        "queued_at": _iso(a.queued_at),
        "started_at": _iso(a.started_at),
        "finished_at": _iso(a.finished_at),
        "result": a.result,
        "idempotency_key": a.idempotency_key,
        "expires_at": _iso(a.expires_at),
        "created_at": _iso(a.created_at),
        "notice": UNKNOWN_COPY if a.state == OUTCOME_UNKNOWN else None,
    }


def _audit(
    session: Any, *, actor: int, action: str, row: SupportActionModel, note: str = ""
) -> None:
    ticket = f"ticket={row.ticket_id}; " if row.ticket_id else ""
    session.add(
        AdminActionLogModel(
            actor_user_id=actor,
            action=action,
            target_user_id=row.target_user_id,
            target_organization_id=row.organization_id,
            note=(
                f"{ticket}action={row.id}; kind={row.kind}; version={row.version}; "
                f"params={row.params}; {note}"
            )[:500],
        )
    )


def _emit_props(row: SupportActionModel, **extra: Any) -> dict[str, Any]:
    return {"status": row.state, "reason_code": row.kind, **extra}


async def _scope(
    session: Any,
    *,
    command: commands.Command,
    organization_id: int,
    target_user_id: int | None,
    ticket_id: int | None,
) -> SupportTicketModel | None:
    """Verify the target: a member of the workspace; a ticket from it; the
    customer's own request where the command needs one."""
    if target_user_id is not None:
        member = await session.scalar(
            select(OrganizationMembershipModel.id).where(
                OrganizationMembershipModel.organization_id == organization_id,
                OrganizationMembershipModel.user_id == target_user_id,
            )
        )
        if member is None:
            raise commands.InvalidTarget("That person is not in this workspace.")
    ticket = None
    if ticket_id is not None:
        ticket = await session.get(SupportTicketModel, ticket_id)
        if ticket is None or ticket.organization_id != organization_id:
            raise commands.InvalidTarget("That ticket is not from this workspace.")
    if command.needs_customer_request:
        if ticket is None:
            raise commands.InvalidTarget(
                "This changes the customer's own things: attach their ticket asking for it."
            )
        if target_user_id is not None and ticket.requester_user_id != target_user_id:
            raise commands.InvalidTarget(
                "The ticket must be from the person it changes."
            )
    return ticket


async def preview(
    *,
    kind: str,
    organization_id: int,
    target_user_id: int | None,
    ticket_id: int | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    """The exact action, before it is requested. Raises InvalidTarget."""
    command = commands.get(kind)
    state, reason = command.state_for(organization_id)
    if state != "available":
        raise commands.InvalidTarget(reason or "Not available.")
    parsed = commands.parse(command, params)
    async with db_client.async_session() as session:
        ticket = await _scope(
            session,
            command=command,
            organization_id=organization_id,
            target_user_id=target_user_id,
            ticket_id=ticket_id,
        )
    assert command.preview is not None
    shown = await command.preview(
        commands.Context(
            organization_id=organization_id,
            target_user_id=target_user_id,
            params=parsed,
            ticket_requester_id=getattr(ticket, "requester_user_id", None),
        )
    )
    clean_params = parsed.model_dump()
    shown["target"] = {
        "organization_id": organization_id,
        "user_id": target_user_id,
        "ticket_id": ticket_id,
    }
    shown["environment"] = constants.ENVIRONMENT
    shown["approvers"] = (
        "A second staff member"
        if command.approver_role == commands.SUPPORT
        else "A second staff member who is a superadmin"
    )
    shown["version"] = commands.version_of(
        kind, organization_id, target_user_id, clean_params, shown.get("captured", {})
    )
    shown["params"] = clean_params
    return shown


async def request(
    *,
    staff: UserModel,
    kind: str,
    organization_id: int,
    target_user_id: int | None,
    ticket_id: int | None,
    params: dict[str, Any],
    reason: str,
    idempotency_key: str,
    expected_version: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record the request. Returns (action, created)."""
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ActionError("Say why, in a few words.")
    key = (idempotency_key or "").strip()[:80]
    if not key:
        raise ActionError("An idempotency key is required.")
    async with db_client.async_session() as session:
        existing = await session.scalar(
            select(SupportActionModel).where(SupportActionModel.idempotency_key == key)
        )
        if existing is not None:
            if existing.requested_by != staff.id:
                raise ActionError("That key belongs to another request.")
            return as_dict(existing), False
    shown = await preview(
        kind=kind,
        organization_id=organization_id,
        target_user_id=target_user_id,
        ticket_id=ticket_id,
        params=params,
    )
    if expected_version is not None and expected_version != shown["version"]:
        raise Changed("This changed since you looked at it. Review the new preview.")
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        inserted = (
            await session.execute(
                insert(SupportActionModel)
                .values(
                    ticket_id=ticket_id,
                    organization_id=organization_id,
                    target_user_id=target_user_id,
                    kind=kind,
                    params=shown.pop("params"),
                    preview=shown,
                    version=shown["version"],
                    reason=reason[:500],
                    state=REQUESTED,
                    environment=str(constants.ENVIRONMENT)[:16],
                    requested_by=staff.id,
                    idempotency_key=key,
                    expires_at=now + REQUEST_TTL,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(constraint="ux_support_actions_key")
                .returning(SupportActionModel.id)
            )
        ).first()
        if inserted is None:
            await session.rollback()
            existing = await session.scalar(
                select(SupportActionModel).where(
                    SupportActionModel.idempotency_key == key
                )
            )
            return as_dict(existing), False
        row = await session.get(SupportActionModel, inserted[0])
        _audit(
            session,
            actor=staff.id,
            action="support_action_requested",
            row=row,
            note=f"reason={reason}",
        )
        await events.emit(
            "support_action_requested",
            session=session,
            user_id=target_user_id,
            organization_id=organization_id,
            properties=_emit_props(row),
        )
        await session.commit()
        await session.refresh(row)
        return as_dict(row), True


async def _load(
    session: Any, action_id: int, *, lock: bool = False
) -> SupportActionModel:
    query = select(SupportActionModel).where(SupportActionModel.id == action_id)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise NotFound("No such action.")
    return row


def _lapsed(row: SupportActionModel, now: datetime) -> bool:
    return row.state in (REQUESTED, APPROVED) and row.expires_at <= now


async def _expire(session: Any, row: SupportActionModel, now: datetime) -> None:
    row.state = EXPIRED
    row.updated_at = now
    row.finished_at = now
    _audit(session, actor=row.requested_by, action="support_action_expired", row=row)


async def get(action_id: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = await _load(session, action_id, lock=True)
        if _lapsed(row, now):
            await _expire(session, row, now)
            await session.commit()
            row = await _load(session, action_id)
        names = await _names(session, {row.requested_by, row.approved_by, row.run_by})
        history = (
            (
                await session.execute(
                    select(AdminActionLogModel)
                    .where(AdminActionLogModel.note.like(f"%action={row.id};%"))
                    .order_by(
                        AdminActionLogModel.created_at.desc(),
                        AdminActionLogModel.id.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        names |= await _names(session, {h.actor_user_id for h in history})
        out = as_dict(row, names)
        out["history"] = [
            {
                "id": h.id,
                "at": _iso(h.created_at),
                "actor": names.get(h.actor_user_id) or f"Staff #{h.actor_user_id}",
                "action": h.action,
            }
            for h in history
        ]
        return out


async def _names(session: Any, ids: set[int | None]) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = await session.execute(
        select(UserModel.id, UserModel.email).where(UserModel.id.in_(ids))
    )
    return {uid: email for uid, email in rows.all() if email}


async def list_actions(
    *, state: str | None = None, ticket_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    query = select(SupportActionModel)
    if state == "pending":
        query = query.where(SupportActionModel.state.in_((REQUESTED, APPROVED)))
    elif state:
        if state not in STATES:
            raise ActionError("Unknown state.")
        query = query.where(SupportActionModel.state == state)
    if ticket_id is not None:
        query = query.where(SupportActionModel.ticket_id == ticket_id)
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    query.order_by(SupportActionModel.id.desc()).limit(min(limit, 200))
                )
            )
            .scalars()
            .all()
        )
        ids: set[int | None] = set()
        for r in rows:
            ids |= {r.requested_by, r.approved_by, r.run_by}
        names = await _names(session, ids)
    now = datetime.now(UTC)
    out = []
    for r in rows:
        row = as_dict(r, names)
        if _lapsed(r, now):
            row["state"] = EXPIRED
        out.append(row)
    return out


async def approve(*, action_id: int, staff: UserModel, version: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = await _load(session, action_id, lock=True)
        if row.requested_by == staff.id:
            raise Forbidden("A second person approves. You asked for this one.")
        command = commands.get(row.kind)
        if _rank(staff) < STAFF_ROLE_RANK[command.approver_role]:
            raise Forbidden(f"Approving this needs the {command.approver_role} role.")
        if _lapsed(row, now):
            await _expire(session, row, now)
            await session.commit()
            raise ActionError("This request expired. Ask for it again.")
        if row.state != REQUESTED:
            raise ActionError(
                f"This action is {row.state.replace('_', ' ')}; it cannot be approved now."
            )
        if version != row.version:
            raise Changed("This changed since you looked at it. Review it again.")
    # The target may have moved since the preview: approve only what is still true.
    fresh = await preview(
        kind=row.kind,
        organization_id=row.organization_id,
        target_user_id=row.target_user_id,
        ticket_id=row.ticket_id,
        params=row.params,
    )
    if fresh["version"] != row.version:
        raise Changed(
            "The customer's account changed since this was requested. "
            "The requester must preview it again."
        )
    async with db_client.async_session() as session:
        hit = (
            await session.execute(
                update(SupportActionModel)
                .where(
                    SupportActionModel.id == action_id,
                    SupportActionModel.state == REQUESTED,
                    SupportActionModel.version == version,
                )
                .values(
                    state=APPROVED,
                    approved_by=staff.id,
                    approved_version=version,
                    approved_at=now,
                    expires_at=now + APPROVAL_TTL,
                    updated_at=now,
                )
                .returning(SupportActionModel.id)
            )
        ).first()
        if hit is None:
            await session.rollback()
            raise Changed("Someone else decided this first. Reload it.")
        row = await _load(session, action_id)
        _audit(session, actor=staff.id, action="support_action_approved", row=row)
        await events.emit(
            "support_action_approved",
            session=session,
            user_id=row.target_user_id,
            organization_id=row.organization_id,
            properties=_emit_props(row),
        )
        await session.commit()
    return await get(action_id)


async def reject(*, action_id: int, staff: UserModel, note: str) -> dict[str, Any]:
    note = (note or "").strip()
    if len(note) < 3:
        raise ActionError("Say why it is refused.")
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = await _load(session, action_id, lock=True)
        if row.requested_by == staff.id:
            raise Forbidden("Withdraw your own request instead.")
        if row.state != REQUESTED:
            raise ActionError(f"This action is {row.state.replace('_', ' ')}.")
        row.state = REJECTED
        row.decided_note = note[:500]
        row.finished_at = now
        row.updated_at = now
        _audit(
            session,
            actor=staff.id,
            action="support_action_rejected",
            row=row,
            note=f"note={note}",
        )
        await session.commit()
    return await get(action_id)


async def withdraw(*, action_id: int, staff: UserModel) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = await _load(session, action_id, lock=True)
        if row.requested_by != staff.id:
            raise Forbidden("Only the person who asked can withdraw it.")
        if row.state not in (REQUESTED, APPROVED):
            raise ActionError(f"This action is {row.state.replace('_', ' ')}.")
        row.state = CANCELLED
        row.finished_at = now
        row.updated_at = now
        _audit(session, actor=staff.id, action="support_action_withdrawn", row=row)
        await session.commit()
    return await get(action_id)


async def revise(
    *, action_id: int, staff: UserModel, params: dict[str, Any]
) -> dict[str, Any]:
    """New parameters: a new version, back to requested, approval cleared."""
    async with db_client.async_session() as session:
        row = await _load(session, action_id)
        if row.requested_by != staff.id:
            raise Forbidden("Only the person who asked can change it.")
        if row.state not in (REQUESTED, APPROVED):
            raise ActionError(
                f"This action is {row.state.replace('_', ' ')}; it cannot change."
            )
    shown = await preview(
        kind=row.kind,
        organization_id=row.organization_id,
        target_user_id=row.target_user_id,
        ticket_id=row.ticket_id,
        params=params,
    )
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = await _load(session, action_id, lock=True)
        if row.state not in (REQUESTED, APPROVED):
            raise ActionError(
                f"This action is {row.state.replace('_', ' ')}; it cannot change."
            )
        previous = row.version
        row.params = shown.pop("params")
        row.preview = shown
        row.version = shown["version"]
        row.state = REQUESTED
        row.approved_by = None
        row.approved_version = None
        row.approved_at = None
        row.expires_at = now + REQUEST_TTL
        row.updated_at = now
        _audit(
            session,
            actor=staff.id,
            action="support_action_revised",
            row=row,
            note=f"was={previous}",
        )
        await session.commit()
    return await get(action_id)


async def run(*, action_id: int, staff: UserModel) -> tuple[dict[str, Any], bool]:
    """Queue an approved action once. Returns (action, queued_now): a repeat
    click while it is queued, running or finished is (action, False)."""
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = await _load(session, action_id, lock=True)
        if row.state in (QUEUED, RUNNING, SUCCEEDED, FAILED, OUTCOME_UNKNOWN):
            return await _get_in(session, row), False
        if staff.id not in (row.requested_by, row.approved_by):
            raise Forbidden("The person who asked or the person who approved runs it.")
        if _lapsed(row, now):
            await _expire(session, row, now)
            await session.commit()
            raise ActionError("The approval expired. Ask for it again.")
        if row.state != APPROVED:
            raise ActionError("This needs a second person's approval first.")
        if row.approved_version != row.version:
            raise Changed(
                "This changed after it was approved. It needs approving again."
            )
        row.state = QUEUED
        row.run_by = staff.id
        row.queued_at = now
        row.updated_at = now
        _audit(session, actor=staff.id, action="support_action_queued", row=row)
        await session.commit()
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    try:
        await enqueue_job(
            FunctionNames.RUN_SUPPORT_ACTION,
            action_id,
            _job_id=f"support-action-{action_id}",
        )
    except Exception as exc:
        logger.error("Support action {} could not be queued: {}", action_id, exc)
        async with db_client.async_session() as session:
            await session.execute(
                update(SupportActionModel)
                .where(
                    SupportActionModel.id == action_id,
                    SupportActionModel.state == QUEUED,
                )
                .values(
                    state=APPROVED,
                    run_by=None,
                    queued_at=None,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
        raise ActionError("Could not be started. Try again.") from exc
    return await get(action_id), True


async def _get_in(session: Any, row: SupportActionModel) -> dict[str, Any]:
    names = await _names(session, {row.requested_by, row.approved_by, row.run_by})
    return as_dict(row, names)


def _run_context(row: SupportActionModel) -> commands.RunContext:
    return commands.RunContext(
        action_id=row.id,
        organization_id=row.organization_id,
        target_user_id=row.target_user_id,
        params=dict(row.params or {}),
        captured=dict((row.preview or {}).get("captured") or {}),
        reason=row.reason,
        requested_by=row.requested_by,
        approved_by=row.approved_by,
        run_by=row.run_by,
    )


async def execute(action_id: int) -> str | None:
    """The worker's half: claim queued -> running, run once, settle, notify.
    Returns the final state, or None when there was nothing to claim."""
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        claimed = (
            await session.execute(
                update(SupportActionModel)
                .where(
                    SupportActionModel.id == action_id,
                    SupportActionModel.state == QUEUED,
                )
                .values(state=RUNNING, started_at=now, updated_at=now)
                .returning(SupportActionModel.id)
            )
        ).first()
        if claimed is None:
            await session.rollback()
            return None
        await session.commit()
        row = await _load(session, action_id)
    command = commands.COMMANDS.get(row.kind)
    ctx = _run_context(row)
    recomputed = commands.version_of(
        row.kind, row.organization_id, row.target_user_id, ctx.params, ctx.captured
    )
    if command is None or command.run is None:
        state, result = (
            FAILED,
            {"summary": "This command cannot run.", "reason_code": "not_runnable"},
        )
    elif recomputed != row.version or row.approved_version != row.version:
        state, result = (
            FAILED,
            {
                "summary": "The parameters do not match what was approved; nothing ran.",
                "reason_code": "version_mismatch",
            },
        )
    else:
        try:
            result = await command.run(ctx)
            state = SUCCEEDED
        except commands.CommandFailed as exc:
            state, result = FAILED, {"summary": str(exc), "reason_code": "refused"}
        except Exception as exc:  # noqa: BLE001 - unknown is a state, not a crash
            logger.exception("Support action {} outcome unknown: {}", action_id, exc)
            state, result = (
                OUTCOME_UNKNOWN,
                {
                    "summary": UNKNOWN_COPY,
                    "reason_code": type(exc).__name__[:64],
                },
            )
    await _settle(action_id, state, result, actor=row.run_by or row.requested_by)
    return state


async def _settle(
    action_id: int,
    state: str,
    result: dict[str, Any],
    *,
    actor: int,
    expect: str = RUNNING,
) -> bool:
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        hit = (
            await session.execute(
                update(SupportActionModel)
                .where(
                    SupportActionModel.id == action_id,
                    SupportActionModel.state == expect,
                )
                .values(
                    state=state,
                    result=result,
                    finished_at=now if state in FINISHED else None,
                    updated_at=now,
                )
                .returning(SupportActionModel.id)
            )
        ).first()
        if hit is None:
            await session.rollback()
            return False
        row = await _load(session, action_id)
        _audit(
            session,
            actor=actor,
            action=f"support_action_{state}",
            row=row,
            note=f"result={result.get('summary', '')}",
        )
        name = {
            SUCCEEDED: "support_action_executed",
            FAILED: "support_action_failed",
            OUTCOME_UNKNOWN: "support_action_failed",
        }.get(state)
        if name:
            await events.emit(
                name,
                session=session,
                user_id=row.target_user_id,
                organization_id=row.organization_id,
                properties=_emit_props(
                    row, reason_code=result.get("reason_code") or row.kind
                ),
            )
        if row.ticket_id and state in (SUCCEEDED, FAILED):
            title = (row.preview or {}).get(
                "customer_summary"
            ) or "A change to your account"
            body = (
                f"Done: {title}."
                if state == SUCCEEDED
                else f"We could not do this: {title}. Support will follow up here."
            )
            session.add(
                SupportMessageModel(
                    ticket_id=row.ticket_id,
                    organization_id=row.organization_id,
                    author_user_id=None,
                    author_kind="system",
                    body=body,
                    created_at=now,
                )
            )
            await session.execute(
                update(SupportTicketModel)
                .where(SupportTicketModel.id == row.ticket_id)
                .values(updated_at=now)
            )
        await session.commit()
        return True


async def reconcile(*, action_id: int, staff: UserModel) -> dict[str, Any]:
    """Settle an ``outcome_unknown`` by checking whether the change is there."""
    async with db_client.async_session() as session:
        row = await _load(session, action_id)
    if row.state != OUTCOME_UNKNOWN:
        raise ActionError("Only an action whose outcome is unknown is reconciled.")
    command = commands.COMMANDS.get(row.kind)
    if command is None or command.reconcile is None:
        raise ActionError("This command cannot be reconciled automatically.")
    found = await command.reconcile(_run_context(row))
    if found is None:
        raise ActionError("Could not tell yet. Try again shortly.")
    state = SUCCEEDED if found else FAILED
    result = {
        "summary": (
            "Reconciled: the change is in place."
            if found
            else "Reconciled: it did not happen. It is safe to request it again."
        ),
        "reason_code": "reconciled",
        "previous": row.result,
    }
    await _settle(action_id, state, result, actor=staff.id, expect=OUTCOME_UNKNOWN)
    return await get(action_id)


async def sweep(now: datetime | None = None) -> dict[str, int]:
    """Runs whose worker died become outcome unknown; lapsed requests and
    approvals become expired."""
    now = now or datetime.now(UTC)
    async with db_client.async_session() as session:
        stale = (
            (
                await session.execute(
                    select(SupportActionModel.id).where(
                        SupportActionModel.state == RUNNING,
                        SupportActionModel.started_at < now - STALE_RUNNING,
                    )
                )
            )
            .scalars()
            .all()
        )
        lapsed = (
            (
                await session.execute(
                    select(SupportActionModel).where(
                        SupportActionModel.state.in_((REQUESTED, APPROVED)),
                        SupportActionModel.expires_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in lapsed:
            await _expire(session, row, now)
        await session.commit()
    unknown = 0
    for action_id in stale:
        async with db_client.async_session() as session:
            row = await _load(session, action_id)
        if await _settle(
            action_id,
            OUTCOME_UNKNOWN,
            {"summary": UNKNOWN_COPY, "reason_code": "worker_lost"},
            actor=row.run_by or row.requested_by,
        ):
            unknown += 1
    return {"outcome_unknown": unknown, "expired": len(lapsed)}
