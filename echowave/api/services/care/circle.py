"""The family circle: who sees what, by the older person's consent.

The older person owns the circle. It lives in their workspace (their
personal space, ideally) and only they change it:

* **Adding someone** is a consent card naming the person, their email and
  exactly what they will see (``SHARES``). Confirming it makes a one-time
  code; the family member accepts it signed in as that email. Nothing is
  shared before both have happened.
* **Seeing more** is another consent card. **Seeing less** and **removing
  someone** happen at once: stopping a share must never wait.
* **What a family member reads** is computed from their active membership
  every time (``family_view``): a kind not in ``shares`` is not queried, so
  there is nothing to filter out afterwards. Revoking hides past alerts too.

The family member never sees the words of a scam check, a phone number, or
anything the person did not share; the data each share reveals is listed in
``SHARES`` and asserted by the tests.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.care_models import (
    CareAlertModel,
    CareCircleMemberModel,
    CareCircleModel,
    CareDoseCallModel,
    CareMedicineModel,
    CareScamCheckModel,
)
from api.services.care import FAMILY_CIRCLE, CareError, NotFound, on

#: What a family member can be shown. Each is one plain sentence on the
#: consent card; nothing outside this list is ever shown to family.
SHARES: dict[str, str] = {
    "medicine_alerts": "Tell them when a medicine call is missed or not answered",
    "medicine_schedule": "Let them see my medicine reminders and today's calls",
    "scam_checks": "Let them see when I checked something for a scam (not what it said)",
    "help_requests": "Tell them when I get stuck with phone help",
}

PROPOSED = "proposed"
INVITED = "invited"
ACTIVE = "active"
REVOKED = "revoked"
DECLINED = "declined"

#: How long an invitation code can be accepted.
INVITE_DAYS = 7
#: No 0/O, 1/I/L: read aloud over the phone without confusion.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
MAX_MEMBERS = 8


def enabled(organization_id: int | None = None) -> bool:
    return on(FAMILY_CIRCLE, organization_id)


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(code: str) -> str:
    clean = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    return hashlib.sha256(f"care-invite:{clean}".encode()).hexdigest()


def new_code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def mask_email(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "their email"
    return f"{local[:1]}•••@{domain}"


def clean_shares(shares: Any) -> list[str]:
    """The shares asked for, checked. Unknown names are refused, never
    dropped: a share somebody meant to give that silently vanished is the
    failure this module must not have."""
    if not isinstance(shares, (list, tuple)):
        raise CareError("Choose what to share.")
    out: list[str] = []
    for share in shares:
        if share not in SHARES:
            raise CareError(f"{share!r} is not something that can be shared.")
        if share not in out:
            out.append(share)
    return out


def describe_shares(shares: list[str]) -> str:
    return "; ".join(SHARES[s] for s in shares if s in SHARES) or "nothing yet"


async def ensure_circle(organization_id: int, person_user_id: int) -> CareCircleModel:
    async with db_client.async_session() as session:
        await session.execute(
            insert(CareCircleModel)
            .values(organization_id=organization_id, person_user_id=person_user_id)
            .on_conflict_do_nothing(constraint="uq_care_circle_person")
        )
        await session.commit()
        return (
            await session.execute(
                select(CareCircleModel).where(
                    CareCircleModel.organization_id == organization_id,
                    CareCircleModel.person_user_id == person_user_id,
                )
            )
        ).scalar_one()


async def _members(session, circle_id: int, organization_id: int) -> list[Any]:
    return list(
        (
            await session.scalars(
                select(CareCircleMemberModel)
                .where(
                    CareCircleMemberModel.circle_id == circle_id,
                    CareCircleMemberModel.organization_id == organization_id,
                )
                .order_by(CareCircleMemberModel.id)
            )
        ).all()
    )


def member_dict(row: Any) -> dict[str, Any]:
    """A member as the older person sees it. Never the code."""
    return {
        "id": row.id,
        "name": row.name,
        "email": row.email,
        "shares": list(row.shares or []),
        "pending_shares": list(row.pending_shares or []),
        "status": row.status,
        "consent_event_id": row.consent_event_id,
        "invite_expires_at": (
            row.invite_expires_at.isoformat() if row.invite_expires_at else None
        ),
        "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
    }


async def my_circle(organization_id: int, user_id: int) -> dict[str, Any]:
    circle = await ensure_circle(organization_id, user_id)
    async with db_client.async_session() as session:
        members = await _members(session, circle.id, organization_id)
    return {
        "id": circle.id,
        "display_name": circle.display_name,
        "members": [member_dict(m) for m in members if m.status != DECLINED],
        "shares": [{"key": k, "label": v} for k, v in SHARES.items()],
    }


async def set_display_name(organization_id: int, user_id: int, name: str) -> None:
    name = (name or "").strip()[:60]
    circle = await ensure_circle(organization_id, user_id)
    async with db_client.async_session() as session:
        await session.execute(
            update(CareCircleModel)
            .where(
                CareCircleModel.id == circle.id,
                CareCircleModel.organization_id == organization_id,
            )
            .values(display_name=name or None)
        )
        await session.commit()


async def _member(
    session, organization_id: int, person_user_id: int, member_id: int
) -> Any:
    """One member of this person's own circle, or NotFound. The join on
    the circle is what stops one person changing another's circle in the
    same workspace."""
    row = (
        await session.execute(
            select(CareCircleMemberModel)
            .join(
                CareCircleModel, CareCircleModel.id == CareCircleMemberModel.circle_id
            )
            .where(
                CareCircleMemberModel.id == member_id,
                CareCircleMemberModel.organization_id == organization_id,
                CareCircleModel.organization_id == organization_id,
                CareCircleModel.person_user_id == person_user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("That person is not in your circle.")
    return row


async def _propose(
    organization_id: int, user_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    from api.services import acting
    from api.services.workflow import actions

    with acting.acting_as(user_id):
        result = await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments=arguments,
            in_channel=False,
        )
    if result.get("status") == "not_proposed":
        raise CareError(str(result.get("reason") or "That could not be proposed."))
    return result


async def propose_member(
    organization_id: int,
    user_id: int,
    *,
    name: str,
    email: str,
    shares: list[str],
) -> dict[str, Any]:
    """A family member, proposed: the row waits for the consent card.
    Returns the member and the card's event id."""
    name = (name or "").strip()[:60]
    email = (email or "").strip().lower()
    if not name:
        raise CareError("Say who this is.")
    if not _EMAIL.match(email):
        raise CareError("That does not look like an email address.")
    shares = clean_shares(shares)
    if not shares:
        raise CareError("Choose at least one thing to share.")
    me = await db_client.get_user_by_id(user_id)
    if me is not None and (me.email or "").strip().lower() == email:
        raise CareError("That is your own email. Add someone in your family.")
    circle = await ensure_circle(organization_id, user_id)
    async with db_client.async_session() as session:
        members = await _members(session, circle.id, organization_id)
        live = [m for m in members if m.status in (PROPOSED, INVITED, ACTIVE)]
        if any(m.email == email for m in live):
            raise CareError("That person is already in your circle.")
        if len(live) >= MAX_MEMBERS:
            raise CareError("A circle holds eight people. Remove someone first.")
        row = CareCircleMemberModel(
            circle_id=circle.id,
            organization_id=organization_id,
            name=name,
            email=email,
            shares=[],
            pending_shares=shares,
            status=PROPOSED,
            created_by_user_id=user_id,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        member_id = row.id
    from api.services.workflow import actions

    try:
        result = await _propose(
            organization_id,
            user_id,
            {
                "action": actions.CARE_FAMILY_INVITE,
                "member_id": member_id,
                "person_user_id": user_id,
            },
        )
    except CareError:
        # No card, no consent: the row must not sit "waiting" for a card
        # that does not exist, or block asking again.
        await consent_declined(
            organization_id, {"member_id": member_id, "person_user_id": user_id}
        )
        raise
    event_id = result.get("event_id")
    async with db_client.async_session() as session:
        await session.execute(
            update(CareCircleMemberModel)
            .where(
                CareCircleMemberModel.id == member_id,
                CareCircleMemberModel.organization_id == organization_id,
            )
            .values(consent_event_id=event_id, updated_at=_now())
        )
        await session.commit()
        row = await session.get(CareCircleMemberModel, member_id)
        return {"member": member_dict(row), "event_id": event_id}


async def change_shares(
    organization_id: int, user_id: int, member_id: int, shares: list[str]
) -> dict[str, Any]:
    """Fewer shares take effect now. More is a consent card; until it is
    confirmed the member keeps seeing exactly what they saw before."""
    shares = clean_shares(shares)
    async with db_client.async_session() as session:
        row = await _member(session, organization_id, user_id, member_id)
        if row.status not in (INVITED, ACTIVE):
            raise CareError("Only someone already in your circle can be changed.")
        current = list(row.shares or [])
        if set(shares) <= set(current):
            row.shares = [s for s in current if s in shares]
            row.pending_shares = []
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return {"member": member_dict(row), "event_id": None}
        row.pending_shares = shares
        row.updated_at = _now()
        await session.commit()
    from api.services.workflow import actions

    result = await _propose(
        organization_id,
        user_id,
        {
            "action": actions.CARE_FAMILY_SHARE,
            "member_id": member_id,
            "person_user_id": user_id,
        },
    )
    async with db_client.async_session() as session:
        row = await session.get(CareCircleMemberModel, member_id)
        return {"member": member_dict(row), "event_id": result.get("event_id")}


async def revoke(organization_id: int, user_id: int, member_id: int) -> dict[str, Any]:
    """Stop sharing with someone, now. Their code stops working and they
    stop seeing anything, past alerts included."""
    async with db_client.async_session() as session:
        row = await _member(session, organization_id, user_id, member_id)
        row.status = REVOKED
        row.shares = []
        row.pending_shares = []
        row.invite_code_hash = None
        row.revoked_at = _now()
        row.updated_at = _now()
        await session.commit()
        await session.refresh(row)
        return member_dict(row)


# --- the consent card's halves (called from services/care/cards.py) --------


async def card_args(organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    """What the invite or share card shows and binds, read from the row."""
    member_id = int(arguments.get("member_id") or 0)
    person = int(arguments.get("person_user_id") or 0)
    async with db_client.async_session() as session:
        row = await _member(session, organization_id, person, member_id)
        shares = list(row.pending_shares or [])
        if not shares:
            raise CareError("Nothing new to share.")
        return {
            "member_id": row.id,
            "person_user_id": person,
            "name": row.name,
            "email": row.email,
            "shares": shares,
        }


async def activate_invite(
    organization_id: int, args: dict[str, Any], *, event_id: int | None
) -> dict[str, Any]:
    """The person confirmed: make the code, record the consent, and email the
    code when email is set up. Returns the note and the code."""
    member_id = int(args["member_id"])
    code = new_code()
    async with db_client.async_session() as session:
        row = await _member(
            session, organization_id, int(args["person_user_id"]), member_id
        )
        if row.status != PROPOSED:
            raise CareError("This invitation was already answered.")
        if row.email != args.get("email") or list(row.pending_shares or []) != list(
            args.get("shares") or []
        ):
            raise CareError("This changed after it was shown to you. Ask again.")
        row.status = INVITED
        row.shares = list(args["shares"])
        row.pending_shares = []
        row.invite_code_hash = _hash(code)
        row.invite_expires_at = _now() + timedelta(days=INVITE_DAYS)
        row.consented_at = _now()
        row.consent_event_id = event_id or row.consent_event_id
        row.updated_at = _now()
        await session.commit()
    sent = await _email_code(organization_id, args, code)
    how = (
        f"We emailed it to {mask_email(args['email'])}."
        if sent
        else "Email is not set up here, so please give them this code yourself."
    )
    return {
        "code": code,
        "note": (
            f"Invitation ready for {args['name']}. Their code is {code}, valid "
            f"for {INVITE_DAYS} days. {how} They will see only: "
            f"{describe_shares(list(args['shares']))}."
        ),
    }


async def _email_code(organization_id: int, args: dict[str, Any], code: str) -> bool:
    from api.services.messaging import email

    if not email.email_is_configured():
        return False
    circle_name = "your family member"
    async with db_client.async_session() as session:
        circle = (
            await session.execute(
                select(CareCircleModel).where(
                    CareCircleModel.organization_id == organization_id,
                    CareCircleModel.person_user_id == int(args["person_user_id"]),
                )
            )
        ).scalar_one_or_none()
        if circle is not None and circle.display_name:
            circle_name = circle.display_name
    result = await email.send_email(
        to=str(args["email"]),
        subject=f"{circle_name} added you to their family circle on Decibyl",
        body_text=(
            f"Hello {args['name']},\n\n"
            f"{circle_name} added you to their family circle on Decibyl. "
            f"You will see only: {describe_shares(list(args['shares']))}.\n\n"
            f"Sign in to Decibyl with this email address and enter the code "
            f"{code} under Care -> Family. It works for {INVITE_DAYS} days.\n\n"
            "Decibyl will never ask you for an OTP or a password."
        ),
    )
    return bool(getattr(result, "ok", False))


async def apply_share(organization_id: int, args: dict[str, Any]) -> str:
    async with db_client.async_session() as session:
        row = await _member(
            session,
            organization_id,
            int(args["person_user_id"]),
            int(args["member_id"]),
        )
        if row.status not in (INVITED, ACTIVE):
            raise CareError("That person is no longer in your circle.")
        if list(row.pending_shares or []) != list(args.get("shares") or []):
            raise CareError("This changed after it was shown to you. Ask again.")
        row.shares = list(args["shares"])
        row.pending_shares = []
        row.consented_at = _now()
        row.updated_at = _now()
        await session.commit()
    return f"{args['name']} now sees: {describe_shares(list(args['shares']))}."


async def unshare(organization_id: int, args: dict[str, Any]) -> None:
    """Undo of an invite or a widening that ran: back to what it was."""
    async with db_client.async_session() as session:
        row = await _member(
            session,
            organization_id,
            int(args["person_user_id"]),
            int(args["member_id"]),
        )
        if args.get("previous_shares") is not None and row.status in (INVITED, ACTIVE):
            row.shares = list(args["previous_shares"])
        else:
            row.status = REVOKED
            row.shares = []
            row.invite_code_hash = None
            row.revoked_at = _now()
        row.updated_at = _now()
        await session.commit()


async def consent_declined(organization_id: int, args: dict[str, Any]) -> None:
    """The person said no (or took the press back): nothing is shared."""
    async with db_client.async_session() as session:
        try:
            row = await _member(
                session,
                organization_id,
                int(args.get("person_user_id") or 0),
                int(args.get("member_id") or 0),
            )
        except NotFound:
            return
        row.pending_shares = []
        if row.status == PROPOSED:
            row.status = DECLINED
        row.updated_at = _now()
        await session.commit()


# --- the family member's side ---------------------------------------------


async def accept(user_id: int, code: str) -> dict[str, Any]:
    """A family member enters their code, signed in as the invited email.

    The only place a circle is found across workspaces, and it is found by
    the code's hash and then checked against the signed-in person's email:
    a code read off somebody else's screen is no use to them.
    """
    me = await db_client.get_user_by_id(user_id)
    my_email = ((me.email if me else None) or "").strip().lower()
    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(CareCircleMemberModel).where(
                    CareCircleMemberModel.invite_code_hash == _hash(code)
                )
            )
        ).scalar_one_or_none()
        if row is None or row.status != INVITED:
            raise NotFound("That code did not work. Check it, or ask for a new one.")
        expires = row.invite_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires is not None and expires <= _now():
            raise CareError("That code has expired. Ask for a new one.")
        if not my_email or my_email != row.email:
            raise CareError(
                "This invitation is for a different email. Sign in with the "
                "email it was sent to."
            )
        from api.services.auth.email_verification import verification_is_enforceable

        if (
            verification_is_enforceable()
            and getattr(me, "email_verified_at", None) is None
        ):
            # The email is the second half of the key; where it can be
            # proved, it must be, or a stranger could sign up as it.
            raise CareError(
                "Verify your email address first, then enter the code again."
            )
        circle = await session.get(CareCircleModel, row.circle_id)
        if circle is None or circle.organization_id != row.organization_id:
            raise NotFound("That code did not work.")
        if circle.person_user_id == user_id:
            raise CareError("You cannot join your own circle.")
        accepted = {
            "person": circle.display_name or "Your family member",
            "shares": list(row.shares or []),
        }
        row.member_user_id = user_id
        row.status = ACTIVE
        row.accepted_at = _now()
        row.invite_code_hash = None
        row.updated_at = _now()
        await session.commit()
        return accepted


async def _memberships(session, user_id: int) -> list[tuple[Any, Any]]:
    return list(
        (
            await session.execute(
                select(CareCircleMemberModel, CareCircleModel)
                .join(
                    CareCircleModel,
                    CareCircleModel.id == CareCircleMemberModel.circle_id,
                )
                .where(
                    CareCircleMemberModel.member_user_id == user_id,
                    CareCircleMemberModel.status == ACTIVE,
                    CareCircleModel.organization_id
                    == CareCircleMemberModel.organization_id,
                )
                .order_by(CareCircleMemberModel.id)
            )
        ).all()
    )


async def family_view(user_id: int, *, now: datetime | None = None) -> list[dict]:
    """Everyone this person cares for, and only what each one shared.

    Each kind below is read only when its share is present; a share that is
    absent is never queried, so there is nothing to forget to filter.
    """
    now = now or _now()
    since = now - timedelta(days=14)
    out: list[dict] = []
    async with db_client.async_session() as session:
        for member, circle in await _memberships(session, user_id):
            if not enabled(circle.organization_id):
                continue
            shares = list(member.shares or [])
            entry: dict[str, Any] = {
                "member_id": member.id,
                "person": circle.display_name or "Your family member",
                "shares": shares,
                "alerts": [],
                "medicines": None,
                "scam_checks": None,
            }
            alert_shares = [s for s in shares if s in SHARES]
            if alert_shares:
                rows = (
                    await session.scalars(
                        select(CareAlertModel)
                        .where(
                            CareAlertModel.member_id == member.id,
                            CareAlertModel.organization_id == circle.organization_id,
                            CareAlertModel.share.in_(alert_shares),
                            CareAlertModel.created_at >= since,
                        )
                        .order_by(CareAlertModel.created_at.desc())
                        .limit(30)
                    )
                ).all()
                entry["alerts"] = [
                    {
                        "id": a.id,
                        "kind": a.kind,
                        "title": a.title,
                        "at": a.created_at.isoformat(),
                        "read": a.read_at is not None,
                    }
                    for a in rows
                ]
            if "medicine_schedule" in shares:
                meds = (
                    await session.scalars(
                        select(CareMedicineModel)
                        .where(
                            CareMedicineModel.organization_id == circle.organization_id,
                            CareMedicineModel.person_user_id == circle.person_user_id,
                            CareMedicineModel.state.in_(("active", "paused")),
                        )
                        .order_by(CareMedicineModel.id)
                    )
                ).all()
                doses = (
                    await session.scalars(
                        select(CareDoseCallModel)
                        .where(
                            CareDoseCallModel.organization_id == circle.organization_id,
                            CareDoseCallModel.medicine_id.in_(
                                [m.id for m in meds] or [0]
                            ),
                            CareDoseCallModel.due_at >= now - timedelta(hours=24),
                        )
                        .order_by(CareDoseCallModel.due_at)
                    )
                ).all()
                # No phone number: what family need is the medicine and
                # whether it was taken, not the number it rings.
                entry["medicines"] = [
                    {
                        "id": m.id,
                        "label": m.label,
                        "times": list(m.times or []),
                        "timezone": m.timezone,
                        "state": m.state,
                        "doses": [
                            {
                                "due_at": d.due_at.isoformat(),
                                "state": d.state,
                            }
                            for d in doses
                            if d.medicine_id == m.id
                        ],
                    }
                    for m in meds
                ]
            if "scam_checks" in shares:
                checks = (
                    await session.scalars(
                        select(CareScamCheckModel)
                        .where(
                            CareScamCheckModel.organization_id
                            == circle.organization_id,
                            CareScamCheckModel.user_id == circle.person_user_id,
                            CareScamCheckModel.created_at >= since,
                        )
                        .order_by(CareScamCheckModel.created_at.desc())
                        .limit(20)
                    )
                ).all()
                entry["scam_checks"] = [
                    {
                        "kind": c.kind,
                        "verdict": c.verdict,
                        "signals": list(c.signals or []),
                        "at": c.created_at.isoformat(),
                    }
                    for c in checks
                ]
            out.append(entry)
    return out


async def mark_alert_read(user_id: int, alert_id: int) -> None:
    async with db_client.async_session() as session:
        mine = {m.id for m, _ in await _memberships(session, user_id)}
        alert = await session.get(CareAlertModel, alert_id)
        if alert is None or alert.member_id not in mine:
            raise NotFound("That alert is not here.")
        alert.read_at = alert.read_at or _now()
        await session.commit()


# --- telling the family ----------------------------------------------------


async def alert(
    organization_id: int,
    person_user_id: int,
    *,
    kind: str,
    share: str,
    title: str,
    subject_id: int | None,
    only_member_ids: list[int] | None = None,
) -> int:
    """Tell every active member who has ``share``. At most once per member
    per (kind, subject). Returns how many were told. Never raises: a failed
    alert is logged; the thing it is about has already happened."""
    from loguru import logger

    if share not in SHARES:
        raise KeyError(share)
    if not enabled(organization_id):
        return 0
    try:
        async with db_client.async_session() as session:
            circle = (
                await session.execute(
                    select(CareCircleModel).where(
                        CareCircleModel.organization_id == organization_id,
                        CareCircleModel.person_user_id == person_user_id,
                    )
                )
            ).scalar_one_or_none()
            if circle is None:
                return 0
            told = 0
            for member in await _members(session, circle.id, organization_id):
                if member.status != ACTIVE or share not in (member.shares or []):
                    continue
                if only_member_ids is not None and member.id not in only_member_ids:
                    continue
                inserted = (
                    await session.execute(
                        insert(CareAlertModel)
                        .values(
                            organization_id=organization_id,
                            circle_id=circle.id,
                            member_id=member.id,
                            kind=kind,
                            share=share,
                            title=title[:300],
                            subject_id=subject_id,
                            created_at=_now(),
                        )
                        .on_conflict_do_nothing(constraint="uq_care_alert_once")
                        .returning(CareAlertModel.id)
                    )
                ).first()
                told += 1 if inserted else 0
            await session.commit()
            return told
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.error("Could not alert the family circle: {}", exc)
        return 0


async def person_name(organization_id: int, person_user_id: int) -> str:
    """What alerts call the person: their circle name, else their name."""
    async with db_client.async_session() as session:
        circle = (
            await session.execute(
                select(CareCircleModel).where(
                    CareCircleModel.organization_id == organization_id,
                    CareCircleModel.person_user_id == person_user_id,
                )
            )
        ).scalar_one_or_none()
    if circle is not None and circle.display_name:
        return circle.display_name
    return "Your family member"


async def alertable_members(
    organization_id: int, person_user_id: int, member_ids: list[int]
) -> list[dict[str, Any]]:
    """The named members, checked to be in this person's circle (invited or
    active) and sharing medicine alerts. Raises for any that is not, rather
    than dropping it: a family member somebody expects to be told and who
    silently will not be is the worst failure here."""
    if not member_ids:
        return []
    circle = await ensure_circle(organization_id, person_user_id)
    async with db_client.async_session() as session:
        members = {m.id: m for m in await _members(session, circle.id, organization_id)}
    out = []
    for member_id in member_ids:
        m = members.get(int(member_id))
        if m is None or m.status not in (INVITED, ACTIVE):
            raise CareError("One of the people to tell is not in your circle.")
        if "medicine_alerts" not in (m.shares or []):
            raise CareError(
                f"{m.name} has not been given missed-medicine alerts. Share "
                "that with them first."
            )
        out.append({"id": m.id, "name": m.name, "status": m.status})
    return out
