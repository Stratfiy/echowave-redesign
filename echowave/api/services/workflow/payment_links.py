"""A payment an agent asked for, confirmed back in the conversation.

Agents already send payment links from the business's own Razorpay account
(the ``send-the-payment-link`` skill) and can look one up when a customer
says they paid. What they could not do is notice the payment: the customer
paid, and nobody on the thread knew until someone checked by hand. This
closes that loop, the way voice-and-payments products in India now do --
the link goes out in the conversation and the payment is confirmed in it.

**Recorded where it is made.** ``record_created`` runs when the
connected-app call that creates a Razorpay payment link succeeds
(``composio.client.execute_tool``), whatever called it -- an agent on a
call, an agent in a chat, Decibyl itself. It keeps the link, the amount and
the conversation (the run in the context, and through it the agent and
where the customer is). It never raises: the link was created either way.

**Watched until paid.** ``watch`` runs every two minutes. For each open
link it asks the business's Razorpay account, through the same connection
that made it, whether it has been paid. The action that reads a payment
link is found in the app's own catalogue, and its argument in the action's
own schema -- never a name guessed here -- so a vendor rename leaves the
link "cannot be confirmed automatically" and the owner told, rather than a
watch that silently never fires. Links older than ``WATCH_FOR`` stop being
watched.

**Confirmed where the customer is.** On payment, the owner sees "₹500
received" on the agent's thread. A customer on Instagram is thanked in the
same conversation; on other channels the owner is told the customer was not
messaged, so they can do it if they want to.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from api.db import db_client
from api.services import features

FLAG = "payment_confirmations"
CREATE_SLUG = "RAZORPAY_CREATE_PAYMENT_LINK"
TOOLKIT = "razorpay"

#: How long a link is watched, and how often each is checked.
WATCH_FOR = timedelta(days=3)
CHECK_EVERY = timedelta(minutes=2)
#: Links checked in one pass, across every workspace.
BATCH = 50

#: Statuses Razorpay reports for a payment link, as we file them.
PAID = "paid"
EXPIRED = "expired"
CANCELLED = "cancelled"
UNVERIFIABLE = "unverifiable"
_FINAL = {"paid": PAID, "expired": EXPIRED, "cancelled": CANCELLED}

_FETCH = re.compile(r"^RAZORPAY_(FETCH|GET)_(A_)?PAYMENT_LINK(_BY_ID)?$")


def _find(data: Any, key: str, depth: int = 0) -> Any:
    """The first value of ``key`` in a nested reply (Composio wraps the
    vendor's body in one or two layers that vary by action)."""
    if depth > 4:
        return None
    if isinstance(data, dict):
        if key in data and data[key] not in (None, ""):
            return data[key]
        for value in data.values():
            found = _find(value, key, depth + 1)
            if found not in (None, ""):
                return found
    if isinstance(data, list):
        for value in data[:5]:
            found = _find(value, key, depth + 1)
            if found not in (None, ""):
                return found
    return None


def _link_id(data: Any) -> str | None:
    value = _find(data, "id")
    if isinstance(value, str) and value.startswith("plink_"):
        return value
    # Some wrappers name it explicitly.
    for key in ("payment_link_id", "link_id"):
        value = _find(data, key)
        if isinstance(value, str) and value:
            return value
    return None


def _current_run_id() -> int | None:
    """The run the caller is serving, if any (a call, a chat turn)."""
    from pipecat.utils.run_context import get_current_run_id

    try:
        return int(get_current_run_id())  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def _where(run_id: int | None, organization_id: int) -> dict[str, Any]:
    if run_id is None:
        return {}
    run = await db_client.get_workflow_run(run_id, organization_id=organization_id)
    if run is None:
        return {"workflow_run_id": None}
    context = dict(run.initial_context or {})
    channel = str(context.get("channel") or context.get("provider") or "") or None
    if channel == "instagram":
        recipient = str(context.get("instagram_user_id") or "") or None
    else:
        from api.services.messaging.follow_up import CALLER_NUMBER_KEYS

        gathered = dict(run.gathered_context or {})
        recipient = next(
            (
                str(section[key]).strip()
                for section in (context, gathered)
                for key in CALLER_NUMBER_KEYS
                if section.get(key) and str(section[key]).strip()
            ),
            None,
        )
    return {
        "workflow_run_id": run.id,
        "workflow_id": run.workflow_id,
        "channel": channel,
        "recipient": recipient,
    }


async def record_created(
    *,
    organization_id: int | None,
    arguments: dict[str, Any],
    data: Any,
    connected_account_id: str | None,
    user_id: int | None,
) -> None:
    """Keep a link an agent just created. Never raises."""
    try:
        if not organization_id or not features.is_on(FLAG, organization_id):
            return
        link_id = _link_id(data)
        if not link_id:
            logger.warning("A payment link was created but no id came back to watch.")
            return
        amount = _find(data, "amount") or arguments.get("amount") or 0
        where = await _where(_current_run_id(), organization_id)
        await db_client.record_payment_link(
            organization_id=organization_id,
            link_id=link_id,
            short_url=str(_find(data, "short_url") or "")[:512],
            amount_minor=int(amount or 0),
            currency=str(_find(data, "currency") or arguments.get("currency") or "INR")[
                :8
            ],
            description=str(
                _find(data, "description") or arguments.get("description") or ""
            )[:255],
            connected_account_id=connected_account_id,
            acting_user_id=user_id,
            **where,
        )
    except Exception as exc:  # noqa: BLE001 - the link exists either way
        logger.warning("Could not record a payment link to watch: {}", exc)


def money(amount_minor: int, currency: str) -> str:
    whole = amount_minor / 100
    shown = f"{whole:,.0f}" if whole == int(whole) else f"{whole:,.2f}"
    return f"₹{shown}" if currency.upper() == "INR" else f"{currency.upper()} {shown}"


async def fetch_action(organization_id: int) -> tuple[str, str] | None:
    """(slug, argument name) of the action that reads one payment link, from
    the app's own catalogue and the action's own schema; None when there is
    none to be found."""
    from api.services.integrations.composio import schema
    from api.services.integrations.composio.client import toolkit_actions

    actions = await toolkit_actions(TOOLKIT, limit=200)
    slugs = [a["slug"] for a in actions or [] if _FETCH.match(a.get("slug") or "")]
    if not slugs:
        return None
    slug = slugs[0]
    properties, required = schema.properties_of(await schema.input_schema(slug))
    names = list(required) + [n for n in properties if n not in required]
    argument = next((n for n in names if "id" in n.lower()), None)
    if argument is None:
        return None
    return slug, argument


async def _tell_owner(row: Any, summary: str, payload: dict[str, Any]) -> None:
    from api.enums import AgentEventActor, AgentEventKind
    from api.services.workflow import agent_timeline

    await agent_timeline.record(
        organization_id=row.organization_id,
        workflow_id=row.workflow_id,
        workflow_run_id=row.workflow_run_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary=summary,
        payload={"payment_link": row.link_id, **payload},
        in_channel=False,
    )


async def _thank_customer(row: Any) -> bool:
    """Thank the customer in their conversation, where an agent may write
    back. True when they were messaged."""
    if row.channel != "instagram" or not row.recipient:
        return False
    from api.services.messaging import instagram

    account = await db_client.get_instagram_account(row.organization_id)
    if account is None:
        return False
    await instagram.send_text(
        account,
        to=row.recipient,
        text=(
            f"Payment of {money(row.amount_minor, row.currency)} received. Thank you!"
        ),
    )
    return True


async def paid(row: Any) -> None:
    now = datetime.now(UTC)
    await db_client.update_payment_link(
        row.id,
        organization_id=row.organization_id,
        status=PAID,
        paid_at=now,
        checked_at=now,
    )
    try:
        thanked = await _thank_customer(row)
    except Exception as exc:  # noqa: BLE001 - the owner is still told
        logger.warning("Could not thank the customer for {}: {}", row.link_id, exc)
        thanked = False
    amount = money(row.amount_minor, row.currency)
    what = f" for {row.description}" if row.description else ""
    await _tell_owner(
        row,
        f"{amount} received{what} (payment link {row.link_id}). "
        + (
            "The customer was thanked in the conversation."
            if thanked
            else "The customer was not messaged about it."
        ),
        {"status": PAID, "amount_minor": row.amount_minor, "thanked": thanked},
    )


async def check(row: Any, action: tuple[str, str] | None) -> str:
    """Ask Razorpay about one link and act on the answer. Returns its status."""
    from api.services.integrations.composio.client import execute_tool

    now = datetime.now(UTC)
    if action is None:
        await db_client.update_payment_link(
            row.id,
            organization_id=row.organization_id,
            status=UNVERIFIABLE,
            checked_at=now,
        )
        await _tell_owner(
            row,
            f"The {money(row.amount_minor, row.currency)} payment link "
            f"{row.link_id} cannot be confirmed automatically; check it in "
            "Razorpay when the customer says they paid.",
            {"status": UNVERIFIABLE},
        )
        return UNVERIFIABLE
    slug, argument = action
    result = await execute_tool(
        tool_slug=slug,
        arguments={argument: row.link_id},
        organization_id=row.organization_id,
        connected_account_id=row.connected_account_id,
        user_id=row.acting_user_id,
    )
    if result.get("status") != "success":
        await db_client.update_payment_link(
            row.id, organization_id=row.organization_id, checked_at=now
        )
        return "unknown"
    status = str(_find(result.get("data"), "status") or "").lower()
    if status == PAID:
        await paid(row)
        return PAID
    if status in _FINAL:
        await db_client.update_payment_link(
            row.id,
            organization_id=row.organization_id,
            status=_FINAL[status],
            checked_at=now,
        )
        return _FINAL[status]
    await db_client.update_payment_link(
        row.id, organization_id=row.organization_id, checked_at=now
    )
    return status or "unknown"


async def watch() -> dict[str, int]:
    """One pass over the open links. Never raises."""
    now = datetime.now(UTC)
    counts: dict[str, int] = {}
    try:
        counts["stopped_watching"] = await db_client.expire_payment_links(
            created_before=now - WATCH_FOR
        )
        rows = await db_client.payment_links_to_check(
            created_after=now - WATCH_FOR,
            checked_before=now - CHECK_EVERY,
            limit=BATCH,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Payment link watch could not read its links: {}", exc)
        return counts
    actions: dict[int, tuple[str, str] | None] = {}
    for row in rows:
        if not features.is_on(FLAG, row.organization_id):
            continue
        try:
            if row.organization_id not in actions:
                actions[row.organization_id] = await fetch_action(row.organization_id)
            status = await check(row, actions[row.organization_id])
        except Exception as exc:  # noqa: BLE001 - one link, not the pass
            logger.warning("Payment link {} could not be checked: {}", row.link_id, exc)
            status = "error"
        counts[status] = counts.get(status, 0) + 1
    return counts
