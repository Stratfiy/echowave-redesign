"""Ordering from a list in Chat, through an order card.

The flow, and the one rule it keeps: **nothing is placed until the person
who asked approves the card that shows exactly what will be placed.**

1. ``search`` (a read) -- what the app has for "paneer tikka" or "toned
   milk", as data.
2. ``prepare`` -- the person's list becomes a cart on the app; the app's
   bill is read and checked (``normalise.quote``), saved as a draft whose
   ``digest`` covers the store, every line, every charge, the total, the
   address and the payment method, and an order card is proposed
   (``actions.PLACE_ORDER``). No address yet: the saved addresses come back
   and the model asks which.
3. The person approves on the card. Only they can (``actions.settle``
   refuses anyone else), and only the version on screen.
4. ``place`` -- after the undo window. The draft is claimed
   (proposed -> placing, compare-and-swap, so two jobs place once), the
   cart is quoted again, and if the bill is not exactly what was approved
   nothing is placed and the card says what changed. A connection that
   breaks mid-placement is **outcome unknown** -- "we are checking whether
   this went through; please do not order it again" -- never a retry.
   ``reconcile`` asks the app afterwards and settles the card.

Payment is the app's: Zomato's server returns a UPI step, which the done
card links to. No card details are asked for, stored or sent.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select, update

from api.db import db_client
from api.db.reach_models import ReachOrderDraftModel
from api.services.reach import chips, connections, safety, wire
from api.services.reach.ordering import normalise, providers
from api.services.workflow import agent_timeline

PROPOSED = "proposed"
PLACING = "placing"
PLACED = "placed"
FAILED = "failed"
OUTCOME_UNKNOWN = "outcome_unknown"
CANCELLED = "cancelled"

UNKNOWN_COPY = (
    "We are checking whether this order went through. Please do not order it "
    "again until we know."
)


class OrderError(Exception):
    """Refused, with the reason in words. Nothing was placed."""


class OutcomeUnknown(Exception):
    """The app may or may not have placed it. Never retried."""


# --- reaching the app -------------------------------------------------------


async def _reach(
    organization_id: int, user_id: int, key: str, why: str = ""
) -> tuple[providers.Provider, Any] | dict[str, Any]:
    """The app and this person's connection to it, or the model's answer
    when there is none (a chip goes on the thread)."""
    provider = providers.get(key)
    if provider is None:
        names = ", ".join(p.name for p in providers.PROVIDERS.values())
        return {
            "status": "not_available",
            "reason": f"Ordering works with {names} only.",
        }
    row = await connections.live(
        organization_id, user_id, connections.ORDERING, provider.key
    )
    described = providers.describe(provider, row)
    if described["state"] == providers.CONNECTED:
        return provider, row
    if described["state"] == providers.UNAVAILABLE:
        return {"status": "unavailable", "reason": described["reason"]}
    return await chips.offer(
        organization_id=organization_id,
        user_id=user_id,
        kind=connections.ORDERING,
        provider=provider.key,
        name=provider.name,
        state=described["state"],
        reason=described["reason"],
        why=why,
    )


async def _signed_out(
    organization_id: int, user_id: int, provider: providers.Provider
) -> dict[str, Any]:
    """The app stopped accepting the person's sign-in: a chip to sign in
    again goes on the thread, where they are. "Connect it again" with
    nothing to connect it with was a dead end."""
    answer = await chips.offer(
        organization_id=organization_id,
        user_id=user_id,
        kind=connections.ORDERING,
        provider=provider.key,
        name=provider.name,
        state=providers.AVAILABLE,
        why=f"The {provider.name} sign-in expired.",
    )
    return {**answer, "reason": f"The {provider.name} sign-in has expired."}


async def _call(
    provider: providers.Provider, row: Any, operation: str, arguments: dict[str, Any]
) -> Any:
    offered = {t.get("name") for t in row.tools or []}
    tool = provider.tool_for(operation, offered)
    if tool is None:
        raise wire.ToolRefused(f"{provider.name} does not offer {operation} here.")
    return await connections.call(row, tool, arguments)


def _items(raw: Any) -> list[dict[str, Any]]:
    out = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        item_id = normalise.text(entry.get("item_id") or "", 80)
        try:
            quantity = int(entry.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 0
        if not item_id or not 1 <= quantity <= normalise.MAX_QUANTITY:
            raise OrderError(
                "Each item needs an id from the search and a quantity from 1 to 50."
            )
        out.append({"item_id": item_id, "quantity": quantity})
    if not out:
        raise OrderError("Say which items, from the search results.")
    if len(out) > normalise.MAX_LINES:
        raise OrderError("That is more lines than one order can carry.")
    return out


# --- the draft --------------------------------------------------------------


def digest_of(provider: str, quote: dict[str, Any]) -> str:
    """A hash of exactly what the card shows. The cart id is the app's
    handle, not part of the order, so a fresh cart for the same bill has
    the same digest."""
    shown = {
        "provider": provider,
        "store": quote.get("store"),
        "items": quote.get("items"),
        "charges": quote.get("charges"),
        "discount": quote.get("discount_paise"),
        "total": quote.get("total_paise"),
        "currency": quote.get("currency"),
        "coupon": quote.get("coupon"),
        "address": quote.get("address"),
        "payment": quote.get("payment"),
    }
    canonical = json.dumps(shown, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _draft_quote(row: ReachOrderDraftModel) -> dict[str, Any]:
    return {
        **(row.quote or {}),
        "store": row.store,
        "items": row.items,
        "address": row.address,
        "payment": row.payment,
    }


async def get_draft(
    organization_id: int, user_id: int | None, uuid: str
) -> ReachOrderDraftModel | None:
    """The person's draft. ``user_id=None`` is for the card's own job, which
    checks the owner itself."""
    query = select(ReachOrderDraftModel).where(
        ReachOrderDraftModel.organization_id == organization_id,
        ReachOrderDraftModel.uuid == str(uuid),
    )
    if user_id is not None:
        query = query.where(ReachOrderDraftModel.user_id == user_id)
    async with db_client.async_session() as session:
        return await session.scalar(query)


async def _set(
    row: ReachOrderDraftModel, *, expect: str | None = None, **values: Any
) -> bool:
    values["updated_at"] = datetime.now(UTC)
    query = update(ReachOrderDraftModel).where(
        ReachOrderDraftModel.id == row.id,
        ReachOrderDraftModel.organization_id == row.organization_id,
    )
    if expect is not None:
        query = query.where(ReachOrderDraftModel.status == expect)
    async with db_client.async_session() as session:
        result = await session.execute(query.values(**values))
        await session.commit()
    if result.rowcount:
        for key, value in values.items():
            setattr(row, key, value)
    return bool(result.rowcount)


def detail(
    row: ReachOrderDraftModel, provider_name: str | None = None
) -> dict[str, Any]:
    """The order as its owner sees it on the card."""
    quote = row.quote or {}
    provider = providers.get(row.provider)
    return {
        "id": row.uuid,
        "provider": row.provider,
        "provider_name": provider_name or (provider.name if provider else row.provider),
        "store": row.store,
        "items": row.items,
        "charges": quote.get("charges") or [],
        "discount_paise": quote.get("discount_paise") or 0,
        "subtotal_paise": quote.get("subtotal_paise"),
        "total_paise": quote.get("total_paise"),
        "currency": quote.get("currency") or "INR",
        "coupon": quote.get("coupon"),
        "address": row.address,
        "payment": row.payment,
        "offers": quote.get("offers") or [],
        "status": row.status,
        "digest": row.digest,
        "card_event_id": row.card_event_id,
        "quoted_at": row.quoted_at.isoformat() if row.quoted_at else None,
        "provider_order_id": row.provider_order_id,
        "payment_link": (row.result or {}).get("payment_link"),
        "error": (row.result or {}).get("error"),
    }


def card_label(row: ReachOrderDraftModel) -> str:
    provider = providers.get(row.provider)
    count = sum(int(i.get("quantity") or 0) for i in row.items or [])
    total = normalise.rupees((row.quote or {}).get("total_paise"))
    where = (row.store or {}).get("name") or (
        provider.name if provider else row.provider
    )
    return f"Order from {where} on {provider.name if provider else row.provider}: {count} item{'s' if count != 1 else ''}, {total}"


# --- the model's half ---------------------------------------------------------


async def search(
    *, organization_id: int, user_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    query = normalise.text(arguments.get("query") or "", 120)
    if not query:
        return {"status": "not_available", "reason": "Say what to look for."}
    reached = await _reach(
        organization_id, user_id, arguments.get("app") or "zomato", query
    )
    if isinstance(reached, dict):
        return reached
    provider, row = reached
    try:
        data = await _call(provider, row, providers.SEARCH, {"query": query})
    except wire.NeedsSignIn:
        return await _signed_out(organization_id, user_id, provider)
    except (wire.ToolRefused, wire.WireError) as exc:
        return {
            "status": "error",
            "error": f"{provider.name} search did not work: {exc}",
        }
    results = normalise.search_results(data)
    return safety.as_data(
        source=f"{provider.name} search",
        data={"app": provider.key, "results": results, "count": len(results)},
    )


async def prepare(
    *, organization_id: int, user_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Quote the list and propose the order card. Never places anything."""
    from api.services.workflow import actions

    reached = await _reach(organization_id, user_id, arguments.get("app") or "zomato")
    if isinstance(reached, dict):
        return reached
    provider, row = reached
    try:
        items = _items(arguments.get("items"))
    except OrderError as exc:
        return {"status": "not_proposed", "reason": str(exc)}
    store_id = normalise.text(arguments.get("store_id") or "", 80)
    address_id = normalise.text(arguments.get("address_id") or "", 80)
    if not address_id:
        try:
            saved = normalise.addresses(
                await _call(provider, row, providers.ADDRESSES, {})
            )
        except wire.NeedsSignIn:
            return await _signed_out(organization_id, user_id, provider)
        except (wire.ToolRefused, wire.WireError):
            saved = []
        return {
            "status": "not_proposed",
            "reason": (
                "Ask the person which saved address to deliver to, then call "
                "again with its id. Never pick one for them."
                if saved
                else f"No saved address on {provider.name}; ask the person to add one there."
            ),
            "addresses": saved,
        }
    request = {
        "store_id": store_id,
        "items": items,
        "address_id": address_id,
        "coupon": normalise.text(arguments.get("coupon") or "", 40) or None,
        "payment_method": normalise.text(arguments.get("payment_method") or "", 40)
        or None,
    }
    try:
        quote = normalise.quote(
            await _call(provider, row, providers.QUOTE, request), items
        )
    except normalise.BadQuote as exc:
        return {
            "status": "not_proposed",
            "reason": f"{provider.name}: {exc} Nothing was proposed.",
        }
    except wire.NeedsSignIn:
        return await _signed_out(organization_id, user_id, provider)
    except (wire.ToolRefused, wire.WireError) as exc:
        return {
            "status": "not_proposed",
            "reason": f"{provider.name} could not price that: {exc}",
        }

    draft = await _new_draft(organization_id, user_id, provider, row, quote, request)
    result = await actions.propose(
        organization_id=organization_id,
        workflow_id=None,
        workflow_run_id=None,
        arguments={
            "action": actions.PLACE_ORDER,
            "draft": draft.uuid,
            "owner_user_id": user_id,
            "why": normalise.text(arguments.get("why") or "Asked in the thread", 200),
        },
        in_channel=False,
    )
    if result.get("event_id"):
        await _set(draft, card_event_id=result["event_id"])
    return result


async def _new_draft(
    organization_id: int,
    user_id: int,
    provider: providers.Provider,
    connection: Any,
    quote: dict[str, Any],
    request: dict[str, Any],
) -> ReachOrderDraftModel:
    stored = {
        "charges": quote["charges"],
        "discount_paise": quote["discount_paise"],
        "subtotal_paise": quote["subtotal_paise"],
        "total_paise": quote["total_paise"],
        "currency": quote["currency"],
        "coupon": quote["coupon"],
        "offers": quote["offers"],
        "request": request,
    }
    row = ReachOrderDraftModel(
        organization_id=organization_id,
        user_id=user_id,
        connection_id=connection.id,
        provider=provider.key,
        store=quote["store"],
        items=quote["items"],
        quote=stored,
        address=quote["address"],
        payment=quote["payment"],
        digest=digest_of(provider.key, quote),
        status=PROPOSED,
        provider_cart_id=quote.get("cart_id"),
        quoted_at=datetime.now(UTC),
    )
    async with db_client.async_session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def card_payload(
    organization_id: int, draft_uuid: str, owner_user_id: int | None
) -> dict[str, Any]:
    """What ``actions.resolve`` puts on the order card. The card carries the
    digest, so its version is bound to every figure the person approves.
    Raises :class:`OrderError`."""
    if not owner_user_id:
        raise OrderError(
            "Ordering needs the person who asked; nobody is signed in to this turn."
        )
    row = await get_draft(organization_id, owner_user_id, draft_uuid)
    if row is None or row.status != PROPOSED:
        raise OrderError("That order is not waiting to be placed.")
    if digest_of(row.provider, _draft_quote(row)) != row.digest:
        raise OrderError("That order changed after it was priced.")
    provider = providers.get(row.provider)
    return {
        "args": {"draft": row.uuid, "digest": row.digest, "provider": row.provider},
        "label": card_label(row),
        "effect": (
            f"Places this order on {provider.name if provider else row.provider} "
            f"for {normalise.rupees((row.quote or {}).get('total_paise'))}, to "
            f"{(row.address or {}).get('label') or 'the address shown'}. "
            f"Payment: {(row.payment or {}).get('label') or 'on the app'}. "
            "It cannot be undone once placed."
        ),
        "owner_user_id": owner_user_id,
    }


# --- the card's job -----------------------------------------------------------


async def place(
    *,
    organization_id: int,
    args: dict[str, Any],
    confirmer: int | None,
    idempotency_key: str,
) -> dict[str, Any]:
    """Place the approved order. Returns ``{note, result}``.

    Raises :class:`OrderError` (nothing placed) or :class:`OutcomeUnknown`.
    """
    row = await get_draft(organization_id, None, str(args.get("draft") or ""))
    if row is None:
        raise OrderError("That order is no longer here.")
    if not confirmer or row.user_id != confirmer:
        raise OrderError("Only the person whose order this is can place it.")
    if (
        args.get("digest") != row.digest
        or digest_of(row.provider, _draft_quote(row)) != row.digest
    ):
        raise OrderError(
            "This order changed after it was approved. Nothing was ordered."
        )
    if not await _set(row, expect=PROPOSED, status=PLACING):
        raise OrderError("This order has already been placed or cancelled.")

    async def fail(reason: str) -> None:
        await _set(row, status=FAILED, result={**(row.result or {}), "error": reason})
        raise OrderError(reason)

    provider = providers.get(row.provider)
    connection = await connections.live(
        organization_id, row.user_id, connections.ORDERING, row.provider
    )
    if (
        provider is None
        or connection is None
        or connection.status != connections.CONNECTED
    ):
        await fail("The app is no longer connected. Nothing was ordered.")
    request = dict((row.quote or {}).get("request") or {})
    try:
        fresh = normalise.quote(
            await _call(provider, connection, providers.QUOTE, request),
            request.get("items") or [],
        )
    except (
        normalise.BadQuote,
        wire.ToolRefused,
        wire.WireError,
        wire.NeedsSignIn,
    ) as exc:
        await fail(
            f"{provider.name} could not confirm the bill ({exc}). Nothing was ordered."
        )
    if digest_of(row.provider, fresh) != row.digest:
        was = normalise.rupees((row.quote or {}).get("total_paise"))
        now = normalise.rupees(fresh.get("total_paise"))
        await fail(
            f"The bill changed since you approved it (it was {was}, it is now "
            f"{now}, or an item, the address or the payment changed). Nothing "
            "was ordered; ask me for a fresh card."
        )
    try:
        data = await _call(
            provider,
            connection,
            providers.PLACE,
            {
                "cart_id": fresh.get("cart_id"),
                "payment_method": (row.payment or {}).get("method"),
                "idempotency_key": idempotency_key,
            },
        )
        placed = normalise.placed(data)
    except wire.ToolRefused as exc:
        await fail(f"{provider.name} did not place it: {safety.clean_text(exc, 200)}")
    except (wire.WireError, normalise.BadQuote) as exc:
        logger.warning("Order {} outcome unknown: {}", row.uuid, type(exc).__name__)
        await _set(
            row,
            status=OUTCOME_UNKNOWN,
            result={
                **(row.result or {}),
                "idempotency_key": idempotency_key,
                "error": UNKNOWN_COPY,
            },
        )
        raise OutcomeUnknown(UNKNOWN_COPY) from exc
    await _set(
        row,
        status=PLACED,
        provider_order_id=placed["order_id"],
        result={**(row.result or {}), **placed, "idempotency_key": idempotency_key},
    )
    total = normalise.rupees((row.quote or {}).get("total_paise"))
    pay = (
        " Pay on the app with the link on this card."
        if placed.get("payment_link")
        else f" Payment: {(row.payment or {}).get('label') or 'on the app'}."
    )
    eta = f" Arriving {placed['eta']}." if placed.get("eta") else ""
    return {
        "note": f"Ordered on {provider.name}: order {placed['order_id']}, {total}.{eta}{pay}",
        "result": {
            "order_id": placed["order_id"],
            "payment_link": placed.get("payment_link"),
        },
    }


async def reconcile(*, organization_id: int, user_id: int, uuid: str) -> dict[str, Any]:
    """Ask the app what happened to an order whose outcome is unknown."""
    from api.services.workflow import actions

    row = await get_draft(organization_id, user_id, uuid)
    if row is None:
        raise OrderError("That order is not here.")
    if row.status != OUTCOME_UNKNOWN:
        return detail(row)
    provider = providers.get(row.provider)
    connection = await connections.live(
        organization_id, user_id, connections.ORDERING, row.provider
    )
    if provider is None or connection is None:
        raise OrderError("Connect the app again to check this order.")
    key = (row.result or {}).get("idempotency_key")
    try:
        data = await _call(
            provider, connection, providers.STATUS, {"idempotency_key": key}
        )
    except wire.ToolRefused as exc:
        raise OrderError(f"{provider.name} cannot look this order up: {exc}") from exc
    except (wire.WireError, wire.NeedsSignIn) as exc:
        raise OrderError(
            f"{provider.name} could not be asked just now. Try again."
        ) from exc
    status = (
        str((data or {}).get("status") or "").lower() if isinstance(data, dict) else ""
    )
    if status in ("placed", "confirmed", "preparing", "delivered", "out_for_delivery"):
        placed = normalise.placed(data)
        await _set(
            row,
            status=PLACED,
            provider_order_id=placed["order_id"],
            result={**(row.result or {}), **placed},
        )
        note = f"{provider.name} has it: order {placed['order_id']}. It went through."
        done = True
    elif status in ("not_found", "failed", "cancelled"):
        await _set(
            row,
            status=FAILED,
            result={
                **(row.result or {}),
                "error": f"{provider.name} has no such order.",
            },
        )
        note = f"{provider.name} has no such order: it did not go through."
        done = False
    else:
        raise OrderError(f"{provider.name} does not know yet. Check again in a minute.")
    if row.card_event_id:
        await actions.reconcile(
            organization_id,
            int(row.card_event_id),
            done=done,
            note=note,
            result={"order_id": row.provider_order_id} if done else None,
        )
    return detail(row)


async def revise(
    *, organization_id: int, user_id: int, uuid: str, changes: dict[str, Any]
) -> dict[str, Any]:
    """An edit to a waiting order is a new quote and a new card; the old
    card is declined, so an approval never carries over to new figures."""
    from api.services.workflow import actions

    row = await get_draft(organization_id, user_id, uuid)
    if row is None:
        raise OrderError("That order is not here.")
    if row.status != PROPOSED:
        raise OrderError("This order has already been placed or settled.")
    request = dict((row.quote or {}).get("request") or {})
    if "items" in changes:
        quantities = {
            str(c.get("item_id")): int(c.get("quantity") or 0)
            for c in changes.get("items") or []
            if isinstance(c, dict)
        }
        request["items"] = [
            {**i, "quantity": quantities.get(i["item_id"], i["quantity"])}
            for i in request.get("items") or []
            if quantities.get(i["item_id"], i["quantity"]) > 0
        ]
    for key in ("address_id", "payment_method", "coupon"):
        if key in changes:
            request[key] = normalise.text(changes.get(key) or "", 80) or None
    # The new card goes where the old one is: the edit comes in over HTTP,
    # outside any turn, and without this it landed on the person's original
    # chat while the card they were editing was declined in front of them.
    card = (
        await db_client.get_agent_event(
            int(row.card_event_id), organization_id=organization_id
        )
        if row.card_event_id
        else None
    )
    thread = str(card.thread_id) if card is not None and card.thread_id else None
    with _acting(user_id), agent_timeline.in_thread(thread):
        answer = await prepare(
            organization_id=organization_id,
            user_id=user_id,
            arguments={"app": row.provider, **request, "why": "Edited on the card"},
        )
    if answer.get("status") != "proposed":
        raise OrderError(str(answer.get("reason") or "That edit could not be priced."))
    await _set(row, expect=PROPOSED, status=CANCELLED)
    if row.card_event_id:
        try:
            await actions.settle(
                organization_id=organization_id,
                event_id=int(row.card_event_id),
                verb="decline",
                user_id=user_id,
            )
        except actions.ActionError:
            pass
    return answer


def _acting(user_id: int):
    from api.services import acting

    return acting.acting_as(user_id)


async def cancel_for_card(organization_id: int, args: dict[str, Any]) -> None:
    """The card was declined or undone: the draft can never be placed."""
    row = await get_draft(organization_id, None, str(args.get("draft") or ""))
    if row is not None:
        await _set(row, expect=PROPOSED, status=CANCELLED)


__all__ = [
    "OrderError",
    "OutcomeUnknown",
    "UNKNOWN_COPY",
    "cancel_for_card",
    "card_payload",
    "detail",
    "digest_of",
    "get_draft",
    "place",
    "prepare",
    "reconcile",
    "revise",
    "search",
]
