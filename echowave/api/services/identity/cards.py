"""A person's own identity acts as action cards (launch stream identity).

Every act here sends, pays or deletes, so each goes through the controls
action card (services/workflow/actions.py): an exact preview, a Confirm bound
to the payload version, run once, and "outcome unknown" instead of a blind
retry. Three kinds:

* ``disconnect_app`` -- revoke an app the person (or, for an admin, the
  workspace) connected. Deletes the account at the provider.
* ``send_identity_email`` -- send from the person's Decibyl address.
* ``request_number`` -- buy a phone number for the workspace and assign it
  to a helper. Pays.

Two rules hold for all three:

1. **Only from Settings.** The model's ``propose_action`` arguments reach
   ``actions.resolve`` unfiltered, so these kinds are refused unless the
   proposal came through :func:`propose`, which marks the call. A prompt
   that asks Decibyl to "send from my address" cannot mint one.
2. **Private to the person who asked.** The card carries ``private_to``:
   it is never on a shared timeline, and nobody else -- colleague or admin
   -- can confirm, decline or edit it (``actions._assert_owner``).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from loguru import logger
from sqlalchemy import select, text

from api.db import db_client
from api.db.models import AgentEventModel
from api.enums import AgentEventKind, AgentEventVisibility

_FROM_SETTINGS: ContextVar[bool] = ContextVar(
    "identity_card_from_settings", default=False
)

#: Mirrors actions.py; kept as strings so this module imports nothing from it
#: at load time (actions imports this module lazily).
DISCONNECT_APP = "disconnect_app"
SEND_IDENTITY_EMAIL = "send_identity_email"
REQUEST_NUMBER = "request_number"
KINDS = (DISCONNECT_APP, SEND_IDENTITY_EMAIL, REQUEST_NUMBER)

_EMAIL = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}$")
MAX_SUBJECT = 200
MAX_BODY = 20_000


class CardError(Exception):
    """A card that cannot be proposed or run; ``str(exc)`` is safe to show."""


def is_personal(payload: dict[str, Any]) -> bool:
    """The person's own thing -- their app connection, their Decibyl
    address -- and so theirs alone to approve. A workspace approval rule
    cannot apply to it: nobody but its owner can confirm a private card, so
    a rule naming somebody else would leave it unconfirmable."""
    action = payload.get("action")
    if action == SEND_IDENTITY_EMAIL:
        return True
    return (
        action == DISCONNECT_APP and (payload.get("args") or {}).get("scope") == "mine"
    )


async def _check_approval(organization_id: int, owner: int) -> None:
    """A card that affects the workspace (a number, a workspace connection)
    still answers to the approval matrix -- checked here, before a card is
    made, because only its owner can confirm it afterwards."""
    from api.services.workflow import approvals

    try:
        await approvals.check(
            organization_id, subject=approvals.CARD, amount_paise=None, user_id=owner
        )
    except approvals.ApprovalRequired as exc:
        raise CardError(f"{exc} Ask them to do this from their own Settings.") from exc


@contextmanager
def _from_settings():
    token = _FROM_SETTINGS.set(True)
    try:
        yield
    finally:
        _FROM_SETTINGS.reset(token)


async def waiting(
    organization_id: int, user_id: int, action: str, key: str
) -> int | None:
    """The person's card of this kind about ``key`` still waiting, if any:
    one ask is one card (the controls rule), and a private card is not seen
    by actions' own duplicate check, which reads the shared timeline."""
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(AgentEventModel.id)
            .where(
                AgentEventModel.organization_id == organization_id,
                AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
                AgentEventModel.visibility == AgentEventVisibility.PRIVATE.value,
                text("(agent_events.payload->>'private_to')::int = :uid"),
                text("agent_events.payload->>'action' = :action"),
                text("agent_events.payload->>'key' = :key"),
                text(
                    "agent_events.payload->>'state' IN ('proposed', 'armed', 'running')"
                ),
            )
            .params(uid=user_id, action=action, key=key)
            .order_by(AgentEventModel.id.desc())
            .limit(1)
        )
    return int(row) if row is not None else None


async def propose(
    *, organization_id: int, user_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Put one private card on the person's thread. Returns ``{status,
    event_id}``; ``already_proposed`` with the waiting card's id when one is
    there. Raises CardError with the reason when it cannot be proposed."""
    from api.services.workflow import actions

    action = str(arguments.get("action") or "")
    if action not in KINDS:
        raise CardError("That is not something I can propose.")
    arguments = {**arguments, "owner_user_id": user_id}
    with _from_settings():
        try:
            payload = await resolve(organization_id, arguments)
        except CardError:
            raise
        existing = await waiting(organization_id, user_id, action, payload["key"])
        if existing is not None:
            return {"status": "already_proposed", "event_id": existing}
        if not is_personal(payload):
            await _check_approval(organization_id, user_id)
        told = await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments=arguments,
            in_channel=False,
            include_event_id=True,
        )
    if told.get("status") != "proposed" or not told.get("event_id"):
        raise CardError(str(told.get("reason") or "Could not put that on a card."))
    return {"status": "proposed", "event_id": int(told["event_id"])}


async def mine(organization_id: int, user_id: int, *, limit: int = 20) -> list[Any]:
    """The person's own identity cards in this workspace, newest first."""
    async with db_client.async_session() as session:
        rows = await session.scalars(
            select(AgentEventModel)
            .where(
                AgentEventModel.organization_id == organization_id,
                AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
                AgentEventModel.visibility == AgentEventVisibility.PRIVATE.value,
                text("(agent_events.payload->>'private_to')::int = :uid"),
            )
            .params(uid=user_id)
            .order_by(AgentEventModel.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(rows)


# --- resolve: the exact payload a Confirm approves ---------------------------


def _owner(arguments: dict[str, Any]) -> int:
    owner = arguments.get("owner_user_id")
    if not _FROM_SETTINGS.get() or not isinstance(owner, int) or owner <= 0:
        # Not from Settings: the model asked for it. Refused in the words
        # actions uses for any kind it does not offer.
        raise CardError("That is not something I can propose.")
    return owner


async def resolve(organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    owner = _owner(arguments)
    action = str(arguments.get("action") or "")
    if action == DISCONNECT_APP:
        payload = await _resolve_disconnect(organization_id, owner, arguments)
    elif action == SEND_IDENTITY_EMAIL:
        payload = await _resolve_email(organization_id, owner, arguments)
    elif action == REQUEST_NUMBER:
        payload = await _resolve_number(organization_id, owner, arguments)
    else:
        raise CardError("That is not something I can propose.")
    payload.update(
        {
            "action": action,
            "private_to": owner,
            "why": str(arguments.get("why") or "Asked in Settings")[:300],
            "reversible": False,
            "state": "proposed",
        }
    )
    return payload


async def _resolve_disconnect(
    organization_id: int, owner: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    from api.services.identity import connections

    scope = str(arguments.get("scope") or "")
    account_id = str(arguments.get("connected_account_id") or "")
    found = await connections.find_account(
        organization_id, owner, scope=scope, connected_account_id=account_id
    )
    affected = await connections.dependents(
        organization_id, owner, found["toolkit"], scope
    )
    whose = "yours" if scope == "mine" else "the workspace's"
    effect = (
        f"Decibyl stops reading and acting in {found['app_name']} ({whose}). "
        "Future tasks that need it will ask you to reconnect."
    )
    if affected:
        effect += " Affected: " + "; ".join(affected[:5]) + "."
    return {
        "key": f"{scope}:{account_id}",
        "args": {
            "organization_id": organization_id,
            "owner_user_id": owner,
            "scope": scope,
            "toolkit": found["toolkit"],
            "connected_account_id": account_id,
        },
        "label": f"Disconnect {found['app_name']} ({whose})",
        "effect": effect,
        "affected": affected[:10],
    }


def _clean_email_fields(to: Any, subject: Any, body: Any) -> tuple[str, str, str]:
    to_address = str(to or "").strip()
    if not _EMAIL.match(to_address):
        raise CardError("Say one email address to send to.")
    subject_line = " ".join(str(subject or "").split())[:MAX_SUBJECT]
    if not subject_line:
        raise CardError("Give it a subject.")
    body_text = str(body or "")
    if not body_text.strip():
        raise CardError("Write what to say.")
    if len(body_text) > MAX_BODY:
        raise CardError("That is too long to send as one email.")
    return to_address, subject_line, body_text


async def _resolve_email(
    organization_id: int, owner: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    from api.services.identity import email_identity

    identity = await email_identity.sendable(owner)
    to_address, subject, body = _clean_email_fields(
        arguments.get("to"), arguments.get("subject"), arguments.get("body")
    )
    address = email_identity.address_of(identity.alias)
    return {
        "key": f"email:{identity.id}:{to_address.lower()}:{subject[:40]}",
        "args": {
            "identity_id": identity.id,
            "owner_user_id": owner,
            "from_address": address,
            "to": to_address,
            "subject": subject,
            "body": body,
        },
        "label": f"Send an email from {address} to {to_address}",
        # The exact account first (screen 23: "sending shows the exact
        # account and opens approval").
        "effect": (
            f"Sends from {address}, your Decibyl address, not from a connected "
            "mailbox. It cannot be unsent."
        ),
    }


def revise_email(args: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """An edit to a waiting send: the same checks as a new one."""
    merged = {
        **args,
        **{k: changes[k] for k in ("to", "subject", "body") if k in changes},
    }
    to_address, subject, body = _clean_email_fields(
        merged.get("to"), merged.get("subject"), merged.get("body")
    )
    return {**args, "to": to_address, "subject": subject, "body": body}


async def _resolve_number(
    organization_id: int, owner: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    from api.services.identity import phone

    checked = await phone.check_request(
        organization_id,
        telephony_configuration_id=arguments.get("telephony_configuration_id"),
        address=arguments.get("address"),
        helper_id=arguments.get("helper_id"),
    )
    policy = phone.payment_policy()
    return {
        "key": f"number:{checked['address']}",
        "args": {
            "owner_user_id": owner,
            "telephony_configuration_id": checked["telephony_configuration_id"],
            "address": checked["address"],
            "helper_id": checked["helper_id"],
            "helper_name": checked["helper_name"],
            "payment_policy": policy["key"],
        },
        "label": f"Get {checked['address']} for {checked['helper_name']}",
        "effect": (
            f"Rents this number every month. {policy['who_pays']} "
            f"Amount: {phone.AMOUNT_PLACEHOLDER}."
        ),
    }


# --- execute ----------------------------------------------------------------


async def execute(
    organization_id: int, payload: dict[str, Any], *, event_id: int | None = None
) -> str:
    """Do it, as the card's owner. Raises CardError on a refusal; anything
    else that breaks midway leaves the card's outcome unknown (actions.run)."""
    action = payload.get("action")
    args = dict(payload.get("args") or {})
    owner = payload.get("private_to")
    confirmer = (payload.get("confirmed") or {}).get("by")
    if not owner or confirmer != owner:
        raise CardError("Only the person who asked can confirm this.")
    if action == DISCONNECT_APP:
        from api.services.identity import connections

        return await connections.disconnect(
            organization_id,
            owner,
            scope=str(args.get("scope") or ""),
            connected_account_id=str(args.get("connected_account_id") or ""),
        )
    if action == SEND_IDENTITY_EMAIL:
        from api.services.identity import email_identity

        if event_id is None:
            raise CardError("This send has no card to keep it to once.")
        return await email_identity.send(
            owner,
            identity_id=int(args["identity_id"]),
            card_event_id=event_id,
            to=str(args["to"]),
            subject=str(args["subject"]),
            body=str(args["body"]),
        )
    if action == REQUEST_NUMBER:
        from api.services.identity import phone

        return await phone.provision(organization_id, owner, args)
    logger.warning("Unknown identity card {}", action)
    raise CardError("That is not something that can be done.")
