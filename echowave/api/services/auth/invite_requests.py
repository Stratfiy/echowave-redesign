"""Somebody asks for an invite; an approver says yes or no from their inbox.

The request is the waitlist row (``waitlist_requests``, screen 01): one per
address, written by ``services/shell/early_access.join``. This module is
what happens next.

1. **The approvers hear about it at once.** ``notify_approvers`` mails each
   address in ``INVITE_APPROVER_EMAILS`` with who asked, their note and two
   buttons. Unset, every superadmin is mailed instead and the log says so: a
   request nobody is told about is a request silently lost.
2. **The buttons are signed links, not actions.** Each carries a short-lived
   token (HS256 with ``OSS_JWT_SECRET``, the same signing the Google sign-in
   state uses) naming the request, the action and the approver. Opening the
   link shows a confirm page and changes nothing -- mail scanners and link
   previews fetch every URL in a message, and approving on GET would let a
   virus scanner admit strangers. The decision is a POST.
3. **Single use comes from the row, not a token list.** A token works only
   while the request is still pending; the first decision moves it on, so
   every token for that request -- this approver's, the other approver's,
   the other button -- is spent at once. A second click reads "Already
   approved by ... on ...".
4. **Approve** mints an email-pinned, single-use code
   (``signup_invites.mint``) and mails the person a short welcome with the
   code and a prefilled signup link. **Reject** records the decision and
   mails nobody unless ``INVITE_REJECT_NOTIFY`` is on.

The superadmin invites screen calls ``approve`` / ``reject`` directly; the
mailed links call ``decide``. Same functions, same rules.

Never logged: the invite code. Escaped: everything the person typed, before
it goes into HTML.
"""

from __future__ import annotations

import hashlib
import html
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode

import jwt
from loguru import logger
from sqlalchemy import select, update

from api import constants
from api.db import db_client
from api.db.models import UserModel
from api.db.shell_models import WaitlistRequestModel
from api.enums import StaffRole
from api.services.auth import signup_invites
from api.services.messaging.email import send_email

#: The row's ``status`` values (kept from the waitlist), and what an approver
#: calls them.
PENDING = "waitlisted"
APPROVED = "invited"
REJECTED = "rejected"
STATE_NAMES = {PENDING: "pending", APPROVED: "approved", REJECTED: "rejected"}

APPROVE = "approve"
REJECT = "reject"
ACTIONS = (APPROVE, REJECT)

_AUDIENCE = "decibyl:invite-request-decision"

#: The sign-off on the onboarding mail. The founder's own, as he asked.
SIGN_OFF = "Nithish, Decibyl"

LINK_EXPIRED = "This link has expired. Open the request from the superadmin invites screen instead."
LINK_INVALID = "This link is not valid. Use the buttons in the email, or the superadmin invites screen."


class InvalidDecisionLink(Exception):
    """A link that must not decide anything; ``str(exc)`` is safe to show."""


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class Decision:
    request_id: int
    action: str
    approver_email: str


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def _email_tag(email: str) -> str:
    """Binds a token to the address it was issued for without putting that
    address in the URL."""
    return hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:16]


def issue_token(
    *,
    request_id: int,
    action: str,
    approver_email: str,
    request_email: str,
    now: datetime | None = None,
    ttl: timedelta | None = None,
) -> str:
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    now = now or datetime.now(UTC)
    ttl = ttl or timedelta(days=constants.INVITE_DECISION_TTL_DAYS)
    return jwt.encode(
        {
            "aud": _AUDIENCE,
            "rid": int(request_id),
            "act": action,
            "by": (approver_email or "").strip().lower(),
            "em": _email_tag(request_email),
            "jti": secrets.token_urlsafe(8),
            "iat": now,
            "exp": now + ttl,
        },
        constants.OSS_JWT_SECRET,
        algorithm="HS256",
    )


def _read_claims(token: str) -> dict:
    try:
        claims = jwt.decode(
            token or "",
            constants.OSS_JWT_SECRET,
            algorithms=["HS256"],
            audience=_AUDIENCE,
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidDecisionLink(LINK_EXPIRED) from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidDecisionLink(LINK_INVALID) from exc
    if claims.get("act") not in ACTIONS or not isinstance(claims.get("rid"), int):
        raise InvalidDecisionLink(LINK_INVALID)
    return claims


async def _load_for_token(token: str) -> tuple[Decision, WaitlistRequestModel]:
    claims = _read_claims(token)
    row = await _get(claims["rid"])
    # The request the token was minted for, not merely a row with that id.
    if row is None or not secrets.compare_digest(
        _email_tag(row.email), str(claims.get("em") or "")
    ):
        raise InvalidDecisionLink(LINK_INVALID)
    return (
        Decision(
            request_id=row.id,
            action=claims["act"],
            approver_email=str(claims.get("by") or ""),
        ),
        row,
    )


def decision_link(token: str) -> str:
    base = (constants.UI_APP_URL or "").rstrip("/")
    return f"{base}/invite-requests/decide?{urlencode({'token': token})}"


def signup_link(code: str, email: str) -> str:
    base = (constants.UI_APP_URL or "").rstrip("/")
    return (
        f"{base}/auth/signup?code={quote(signup_invites.display(code))}"
        f"&email={quote(email, safe='')}"
    )


# ---------------------------------------------------------------------------
# Who decides
# ---------------------------------------------------------------------------


async def _superadmins() -> list[UserModel]:
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(UserModel)
                    .where(UserModel.staff_role == StaffRole.SUPERADMIN.value)
                    .order_by(UserModel.id)
                )
            )
            .scalars()
            .all()
        )


async def approver_emails() -> list[str]:
    """``INVITE_APPROVER_EMAILS``, or every superadmin's address."""
    configured = list(dict.fromkeys(constants.INVITE_APPROVER_EMAILS))
    if configured:
        return configured
    fallback = list(
        dict.fromkeys(
            (u.email or "").strip().lower() for u in await _superadmins() if u.email
        )
    )
    logger.warning(
        "INVITE_APPROVER_EMAILS is not set; invite requests go to the {} "
        "superadmin address(es) instead.",
        len(fallback),
    )
    if not fallback:
        logger.error(
            "Nobody to tell about an invite request: INVITE_APPROVER_EMAILS is "
            "unset and there is no superadmin with an email. It is on the "
            "superadmin invites screen."
        )
    return fallback


async def _creator_id(
    decided_by_user_id: int | None, approver_email: str
) -> int | None:
    """Whose name the minted code carries: the approver when they have an
    account here, else the first superadmin."""
    if decided_by_user_id:
        return decided_by_user_id
    if approver_email:
        user = await db_client.get_user_by_email(approver_email)
        if user is not None:
            return user.id
    admins = await _superadmins()
    return admins[0].id if admins else None


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def _one_line(value: str | None, limit: int = 120) -> str:
    """For a subject line: no newlines (header injection), bounded."""
    return _CONTROL.sub(" ", value or "").strip()[:limit]


def first_name(name: str | None) -> str | None:
    parts = _one_line(name).split()
    return parts[0] if parts else None


def _button(href: str, label: str, colour: str) -> str:
    return (
        f'<a href="{html.escape(href, quote=True)}" '
        f'style="display:inline-block;padding:14px 28px;margin:0 8px 8px 0;'
        f"background:{colour};color:#ffffff;text-decoration:none;"
        f'font-weight:600;font-size:16px;border-radius:8px">{label}</a>'
    )


def _wrap(inner: str) -> str:
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,'
        'sans-serif;font-size:15px;line-height:1.6;color:#111827;max-width:560px">'
        f"{inner}</div>"
    )


def compose_approver_mail(
    row: WaitlistRequestModel, *, approve_url: str, reject_url: str
) -> tuple[str, str, str]:
    """(subject, text, html) for the approver."""
    who = _one_line(row.name) or row.email
    subject = f"Invite request: {who}"
    note = (row.first_task or "").strip()
    details = [
        ("Name", (row.name or "").strip() or "Not given"),
        ("Email", row.email),
        ("Note", note or "None"),
    ]
    if row.occupation:
        details.append(("What they do", row.occupation))
    if row.phone:
        details.append(("Phone", row.phone))
    if row.source == "renewal":
        details.append(("Asked from", "an expired or withdrawn invitation"))

    text = (
        f"{who} asked for an invite to Decibyl.\n\n"
        + "\n".join(f"{label}: {value}" for label, value in details)
        + f"\n\nApprove: {approve_url}\nReject: {reject_url}\n\n"
        "Each link opens a page where you confirm. Nothing happens until you do. "
        f"The links work for {constants.INVITE_DECISION_TTL_DAYS} days.\n"
    )
    rows_html = "".join(
        f'<tr><td style="padding:4px 16px 4px 0;color:#6b7280;vertical-align:top">'
        f"{html.escape(label)}</td>"
        f'<td style="padding:4px 0;white-space:pre-wrap">{html.escape(str(value))}</td></tr>'
        for label, value in details
    )
    body_html = _wrap(
        f"<p><strong>{html.escape(who)}</strong> asked for an invite to Decibyl.</p>"
        f'<table style="border-collapse:collapse;margin:16px 0">{rows_html}</table>'
        f'<p style="margin:24px 0">{_button(approve_url, "Approve", "#075A39")}'
        f"{_button(reject_url, 'Reject', '#B42318')}</p>"
        '<p style="color:#6b7280;font-size:13px">Each button opens a page where '
        "you confirm. Nothing happens until you do. The links work for "
        f"{constants.INVITE_DECISION_TTL_DAYS} days.</p>"
    )
    return subject, text, body_html


def compose_onboarding_mail(
    *, name: str | None, code: str, link: str
) -> tuple[str, str, str]:
    """The welcome: very short, friendly, no plans or prices."""
    shown = signup_invites.display(code)
    hello = f"Hi {first_name(name)}," if first_name(name) else "Hi there,"
    subject = "You're in, welcome to Decibyl"
    text = (
        f"{hello}\n\n"
        f"Your invite is ready. Code: {shown}\n\n"
        f"Create your account: {link}\n\n"
        "Sign up with this email; the code works once. "
        "Reply to this email if you get stuck.\n\n"
        f"— {SIGN_OFF}\n"
    )
    body_html = _wrap(
        f"<p>{html.escape(hello)}</p>"
        "<p>Your invite is ready. Code: "
        f'<strong style="font-family:ui-monospace,Menlo,monospace;font-size:17px;'
        f'letter-spacing:1px">{html.escape(shown)}</strong></p>'
        f'<p style="margin:24px 0">{_button(link, "Create your account", "#075A39")}</p>'
        "<p>Sign up with this email; the code works once. "
        "Reply to this email if you get stuck.</p>"
        f"<p>— {html.escape(SIGN_OFF)}</p>"
    )
    return subject, text, body_html


def compose_decline_mail(*, name: str | None) -> tuple[str, str, str]:
    hello = f"Hi {first_name(name)}," if first_name(name) else "Hi there,"
    line = (
        "Thank you for asking about Decibyl. We can't offer you an invite "
        "right now, but we're grateful you're interested."
    )
    subject = "About your Decibyl invite request"
    text = f"{hello}\n\n{line}\n\n— {SIGN_OFF}\n"
    body_html = _wrap(
        f"<p>{html.escape(hello)}</p><p>{html.escape(line)}</p>"
        f"<p>— {html.escape(SIGN_OFF)}</p>"
    )
    return subject, text, body_html


async def notify_approvers(request_id: int) -> int:
    """Mail every approver about one request. Returns how many mails left.

    Never raises: the request is recorded either way and is on the
    superadmin screen.
    """
    try:
        row = await _get(request_id)
        if row is None:
            return 0
        sent = 0
        for approver in await approver_emails():
            tokens = {
                action: issue_token(
                    request_id=row.id,
                    action=action,
                    approver_email=approver,
                    request_email=row.email,
                )
                for action in ACTIONS
            }
            subject, text, body_html = compose_approver_mail(
                row,
                approve_url=decision_link(tokens[APPROVE]),
                reject_url=decision_link(tokens[REJECT]),
            )
            result = await send_email(
                to=approver, subject=subject, body_text=text, body_html=body_html
            )
            if result.ok:
                sent += 1
            else:
                logger.error(
                    "Invite request #{} not mailed to an approver: {}",
                    row.id,
                    result.error,
                )
        return sent
    except Exception:
        logger.exception(
            "Could not notify approvers about invite request #{}", request_id
        )
        return 0


# ---------------------------------------------------------------------------
# Reading and deciding
# ---------------------------------------------------------------------------


async def _get(request_id: int) -> WaitlistRequestModel | None:
    async with db_client.async_session() as session:
        return await session.get(WaitlistRequestModel, request_id)


async def _decider_label(row: WaitlistRequestModel) -> str | None:
    if row.decided_by_email:
        return row.decided_by_email
    if row.decided_by_user_id:
        async with db_client.async_session() as session:
            user = await session.get(UserModel, row.decided_by_user_id)
        if user is not None and user.email:
            return user.email
    return None


def _when(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return f"{value.day} {value:%b %Y} at {value:%H:%M} UTC"


async def describe(row: WaitlistRequestModel) -> dict:
    """What an approver sees about a request."""
    state = STATE_NAMES.get(row.status, row.status)
    decided_by = await _decider_label(row)
    already = None
    if state in ("approved", "rejected"):
        already = f"Already {state}"
        if decided_by:
            already += f" by {decided_by}"
        if row.decided_at:
            already += f" on {_when(row.decided_at)}"
        already += "."
    return {
        "id": row.id,
        "name": row.name,
        "email": row.email,
        "note": row.first_task,
        "occupation": row.occupation,
        "source": row.source,
        "state": state,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decided_by": decided_by,
        "decided_message": already,
    }


async def preview(token: str) -> dict:
    """What the confirm page shows. Changes nothing."""
    decision, row = await _load_for_token(token)
    return {"action": decision.action, "request": await describe(row)}


async def decide(token: str) -> dict:
    """The confirm page's POST: carry out what the link says, once."""
    decision, _row = await _load_for_token(token)
    approver = (
        await db_client.get_user_by_email(decision.approver_email)
        if decision.approver_email
        else None
    )
    act = approve if decision.action == APPROVE else reject
    result = await act(
        decision.request_id,
        decided_by_email=decision.approver_email,
        decided_by_user_id=approver.id if approver is not None else None,
    )
    return {"action": decision.action, **result}


async def _claim(
    request_id: int,
    *,
    to: str,
    decided_by_email: str | None,
    decided_by_user_id: int | None,
) -> WaitlistRequestModel | None:
    """Move a pending request to ``to``. One conditional UPDATE, so two
    approvers clicking at once cannot both win."""
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        result = await session.execute(
            update(WaitlistRequestModel)
            .where(
                WaitlistRequestModel.id == request_id,
                WaitlistRequestModel.status == PENDING,
            )
            .values(
                status=to,
                decided_at=now,
                decided_by_email=(decided_by_email or "").strip().lower() or None,
                decided_by_user_id=decided_by_user_id,
                updated_at=now,
            )
            .returning(WaitlistRequestModel.id)
        )
        won = result.scalar_one_or_none() is not None
        await session.commit()
    return await _get(request_id) if won else None


async def _outcome(row: WaitlistRequestModel, *, changed: bool, **extra) -> dict:
    described = await describe(row)
    if changed:
        message = "Approved." if described["state"] == "approved" else "Rejected."
    else:
        message = described["decided_message"] or "Nothing to do."
    return {
        "outcome": described["state"] if changed else "already_decided",
        "message": message,
        "request": described,
        **extra,
    }


async def approve(
    request_id: int,
    *,
    decided_by_email: str | None = None,
    decided_by_user_id: int | None = None,
) -> dict:
    """Approve a pending request: mint its code and mail the welcome.

    A request that is no longer pending is left alone and the answer says
    who decided it and when. Raises ``NotFound`` for an unknown id.
    """
    if await _get(request_id) is None:
        raise NotFound(request_id)
    row = await _claim(
        request_id,
        to=APPROVED,
        decided_by_email=decided_by_email,
        decided_by_user_id=decided_by_user_id,
    )
    if row is None:
        return await _outcome(await _get(request_id), changed=False)

    try:
        invite = (
            await signup_invites.mint(
                count=1,
                created_by_user_id=await _creator_id(
                    decided_by_user_id, decided_by_email or ""
                ),
                email=row.email,
                note=f"approved from request #{row.id}",
            )
        )[0]
    except Exception:
        # Put it back: an "approved" with no code would strand the person.
        async with db_client.async_session() as session:
            await session.execute(
                update(WaitlistRequestModel)
                .where(WaitlistRequestModel.id == row.id)
                .values(
                    status=PENDING,
                    decided_at=None,
                    decided_by_email=None,
                    decided_by_user_id=None,
                )
            )
            await session.commit()
        raise

    async with db_client.async_session() as session:
        await session.execute(
            update(WaitlistRequestModel)
            .where(WaitlistRequestModel.id == row.id)
            .values(invite_id=invite.id)
        )
        await session.commit()

    link = signup_link(invite.code, row.email)
    subject, text, body_html = compose_onboarding_mail(
        name=row.name, code=invite.code, link=link
    )
    sent = await send_email(
        to=row.email, subject=subject, body_text=text, body_html=body_html
    )
    if not sent.ok:
        # The code stays out of the log; the invite id finds it.
        logger.error(
            "Welcome mail for invite request #{} (invite {}) not sent: {}",
            row.id,
            invite.id,
            sent.error,
        )
    extra = {"mail_sent": sent.ok}
    if not sent.ok:
        # Only to the approver who just approved, so they can pass it on.
        extra["signup_link"] = link
    return await _outcome(await _get(row.id), changed=True, **extra)


async def reject(
    request_id: int,
    *,
    decided_by_email: str | None = None,
    decided_by_user_id: int | None = None,
) -> dict:
    """Reject a pending request. Mails the person only when
    ``INVITE_REJECT_NOTIFY`` is on."""
    if await _get(request_id) is None:
        raise NotFound(request_id)
    row = await _claim(
        request_id,
        to=REJECTED,
        decided_by_email=decided_by_email,
        decided_by_user_id=decided_by_user_id,
    )
    if row is None:
        return await _outcome(await _get(request_id), changed=False)
    mail_sent = False
    if constants.INVITE_REJECT_NOTIFY:
        subject, text, body_html = compose_decline_mail(name=row.name)
        mail_sent = (
            await send_email(
                to=row.email, subject=subject, body_text=text, body_html=body_html
            )
        ).ok
    return await _outcome(row, changed=True, mail_sent=mail_sent)


async def list_requests(limit: int = 200) -> list[dict]:
    """Newest first, every state, for the superadmin screen."""
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    select(WaitlistRequestModel)
                    .order_by(WaitlistRequestModel.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [await describe(r) for r in rows]
