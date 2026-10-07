"""The ledger and finance-only refunds (screens 37-38; handoff 37).

The ledger here is the payments table (what the provider collected) with
each payment's refunds beside it. A refund is the ``refund.request``
command: a finance person asks, a *different* finance person approves, and
the ARQ worker runs it once.

Running it:

1. lock the payment row and recompute what is still refundable -- the
   amount collected less every refund that is not failed, this one excluded
   -- so two approvals at once cannot refund more than was paid;
2. write the ``staff_refunds`` row (one per command, unique), then ask the
   provider with the command's idempotency key as the receipt, so a retried
   job is the same refund at the provider too;
3. read the refund back from the provider. Only a provider status of
   processed is ``refunded``; anything else is ``pending`` until the
   reconciliation sweep reads it again. A call that raised midway is
   ``outcome_unknown`` and is never retried by itself.

The provider is Razorpay when its keys are set; otherwise refunds are
``needs_setup`` and the command refuses at request time. Tests install a
fake with ``use_provider``. Nothing here calls a provider from a test.

The credit balance is not changed by a refund: whether a refunded top-up
also removes its credit is the finance owner's accounting rule (handoff 37,
"finalize accounting rules with the finance owner"), so the result says so.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from loguru import logger
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel, PaymentModel
from api.db.staff_models import StaffRefundModel
from api.services.staff import commands

REQUESTED = "requested"
PENDING = "pending"
REFUNDED = "refunded"
FAILED = "failed"
OUTCOME_UNKNOWN = "outcome_unknown"
#: Refund states that still hold money against the payment.
HOLDING = (REQUESTED, PENDING, REFUNDED, OUTCOME_UNKNOWN)


class RefundProvider(Protocol):
    name: str

    async def create(
        self, *, payment_ref: str, amount_minor: int, currency: str, receipt: str
    ) -> dict[str, Any]: ...

    async def fetch(self, *, payment_ref: str, refund_id: str) -> dict[str, Any]: ...


class RazorpayRefunds:
    """Razorpay's refund API. Only used when its keys are configured."""

    name = "razorpay"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=constants.RAZORPAY_API_BASE,
            auth=(constants.RAZORPAY_KEY_ID or "", constants.RAZORPAY_KEY_SECRET or ""),
            timeout=httpx.Timeout(20.0),
        )

    async def create(self, *, payment_ref, amount_minor, currency, receipt):
        async with self._client() as client:
            resp = await client.post(
                f"/payments/{payment_ref}/refund",
                json={
                    "amount": amount_minor,
                    "receipt": receipt[:40],
                    "speed": "normal",
                },
            )
            resp.raise_for_status()
            body = resp.json()
        return {"id": body.get("id"), "status": body.get("status")}

    async def fetch(self, *, payment_ref, refund_id):
        async with self._client() as client:
            resp = await client.get(f"/payments/{payment_ref}/refunds/{refund_id}")
            resp.raise_for_status()
            body = resp.json()
        return {"id": body.get("id"), "status": body.get("status")}


_OVERRIDE: RefundProvider | None = None


def use_provider(provider: RefundProvider | None) -> None:
    """Install a provider (tests, a local fake); ``None`` restores the
    configured one."""
    global _OVERRIDE
    _OVERRIDE = provider


def provider_for(payment: PaymentModel) -> RefundProvider | None:
    if _OVERRIDE is not None:
        return _OVERRIDE
    if (
        payment.provider == "razorpay"
        and constants.RAZORPAY_KEY_ID
        and constants.RAZORPAY_KEY_SECRET
    ):
        return RazorpayRefunds()
    return None


def provider_state() -> dict[str, Any]:
    if _OVERRIDE is not None:
        return {"state": "available", "provider": _OVERRIDE.name}
    if constants.RAZORPAY_KEY_ID and constants.RAZORPAY_KEY_SECRET:
        return {"state": "available", "provider": "razorpay"}
    return {
        "state": "needs_setup",
        "reason": "No payment provider keys are configured here, so a refund cannot be sent.",
    }


def collected_minor(payment: PaymentModel) -> int:
    """What the provider actually collected, in minor units of the payment's
    currency: gross (net plus GST) for rupees, the dollar amount otherwise."""
    if payment.currency == "INR":
        return int(
            payment.gross_paise
            if payment.gross_paise is not None
            else payment.amount_paise
        )
    return int(payment.amount_minor or 0)


async def _held(
    session: AsyncSession, payment_id: int, exclude_command: int | None = None
) -> int:
    q = select(func.coalesce(func.sum(StaffRefundModel.amount_minor), 0)).where(
        StaffRefundModel.payment_id == payment_id, StaffRefundModel.state.in_(HOLDING)
    )
    if exclude_command is not None:
        q = q.where(StaffRefundModel.command_id != exclude_command)
    return int(await session.scalar(q) or 0)


async def eligible_minor(session: AsyncSession, payment: PaymentModel) -> int:
    return max(0, collected_minor(payment) - await _held(session, payment.id))


def _refund_view(r: StaffRefundModel) -> dict[str, Any]:
    return {
        "id": r.id,
        "payment_id": r.payment_id,
        "amount_minor": r.amount_minor,
        "currency": r.currency,
        "state": r.state,
        "provider": r.provider,
        "provider_refund_id": r.provider_refund_id,
        "command_id": r.command_id,
        "reason_code": r.reason_code,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
        "reconciled_at": r.reconciled_at.isoformat() if r.reconciled_at else None,
    }


def _mask_ref(ref: str | None) -> str | None:
    if not ref:
        return None
    return f"…{ref[-6:]}" if len(ref) > 6 else ref


async def ledger(
    session: AsyncSession,
    *,
    status: str | None = None,
    organization_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Payments newest first, with their refunds, and totals over exactly
    the rows listed so an export matches the view."""
    q = select(PaymentModel, OrganizationModel.name).join(
        OrganizationModel, OrganizationModel.id == PaymentModel.organization_id
    )
    if status:
        q = q.where(PaymentModel.status == status)
    if organization_id is not None:
        q = q.where(PaymentModel.organization_id == organization_id)
    if start:
        q = q.where(PaymentModel.created_at >= start)
    if end:
        q = q.where(PaymentModel.created_at < end)
    rows = (
        await session.execute(q.order_by(PaymentModel.created_at.desc()).limit(limit))
    ).all()
    ids = [p.id for p, _ in rows]
    refunds: dict[int, list[StaffRefundModel]] = {}
    if ids:
        for r in (
            await session.execute(
                select(StaffRefundModel).where(StaffRefundModel.payment_id.in_(ids))
            )
        ).scalars():
            refunds.setdefault(r.payment_id, []).append(r)
    out, totals = [], {}
    for p, org_name in rows:
        mine = refunds.get(p.id, [])
        refunded = sum(r.amount_minor for r in mine if r.state == REFUNDED)
        pending = sum(
            r.amount_minor
            for r in mine
            if r.state in (REQUESTED, PENDING, OUTCOME_UNKNOWN)
        )
        collected = collected_minor(p) if p.status == "paid" else 0
        out.append(
            {
                "id": p.id,
                "organization_id": p.organization_id,
                "organization": org_name or f"Organization {p.organization_id}",
                "status": p.status,
                "refund_state": (
                    "refunded"
                    if refunded and refunded >= collected
                    else "partially_refunded"
                    if refunded
                    else "refund_pending"
                    if pending
                    else None
                ),
                "currency": p.currency,
                "collected_minor": collected,
                "net_paise": p.amount_paise,
                "refunded_minor": refunded,
                "pending_refund_minor": pending,
                "provider": p.provider,
                "payment_ref": _mask_ref(p.payment_id),
                "order_ref": _mask_ref(p.order_id),
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            }
        )
        t = totals.setdefault(
            p.currency,
            {
                "collected_minor": 0,
                "refunded_minor": 0,
                "pending_refund_minor": 0,
                "payments": 0,
            },
        )
        t["collected_minor"] += collected
        t["refunded_minor"] += refunded
        t["pending_refund_minor"] += pending
        t["payments"] += 1
    return {
        "payments": out,
        "totals": totals,
        "refunds": provider_state(),
        "as_of": datetime.now(UTC).isoformat(),
    }


async def transaction(session: AsyncSession, payment_id: int) -> dict[str, Any] | None:
    p = await session.get(PaymentModel, payment_id)
    if p is None:
        return None
    org = await session.get(OrganizationModel, p.organization_id)
    mine = (
        (
            await session.execute(
                select(StaffRefundModel)
                .where(StaffRefundModel.payment_id == p.id)
                .order_by(StaffRefundModel.id)
            )
        )
        .scalars()
        .all()
    )
    timeline = [
        {
            "at": p.created_at.isoformat() if p.created_at else None,
            "event": "order_created",
        }
    ]
    if p.paid_at:
        timeline.append({"at": p.paid_at.isoformat(), "event": "paid"})
    elif p.status == "failed":
        timeline.append({"at": None, "event": "payment_failed"})
    for r in mine:
        timeline.append(
            {
                "at": r.created_at.isoformat(),
                "event": f"refund_{r.state}",
                "refund_id": r.id,
            }
        )
        if r.reconciled_at:
            timeline.append(
                {
                    "at": r.reconciled_at.isoformat(),
                    "event": "refund_reconciled",
                    "refund_id": r.id,
                }
            )
    return {
        "payment": {
            "id": p.id,
            "organization_id": p.organization_id,
            "organization": (org.name if org else None)
            or f"Organization {p.organization_id}",
            "status": p.status,
            "provider": p.provider,
            "currency": p.currency,
            "collected_minor": collected_minor(p) if p.status == "paid" else 0,
            "net_paise": p.amount_paise,
            "gst_paise": {
                "cgst": p.cgst_paise,
                "sgst": p.sgst_paise,
                "igst": p.igst_paise,
            },
            "payment_ref": _mask_ref(p.payment_id),
            "order_ref": _mask_ref(p.order_id),
            "pack_code": p.pack_code,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        },
        "eligible_minor": await eligible_minor(session, p) if p.status == "paid" else 0,
        "refunds": [_refund_view(r) for r in mine],
        "timeline": timeline,
        "refund_provider": provider_state(),
    }


async def export_csv(session: AsyncSession, **filters: Any) -> str:
    """The same rows and definitions as ``ledger``, as CSV. Amounts are exact
    minor units with the currency on every row; nothing is rounded."""
    view = await ledger(session, **filters)
    buf = io.StringIO()
    writer = csv.writer(buf)
    cols = [
        "id",
        "organization_id",
        "status",
        "currency",
        "collected_minor",
        "refunded_minor",
        "pending_refund_minor",
        "refund_state",
        "created_at",
        "paid_at",
    ]
    writer.writerow(cols)
    for row in view["payments"]:
        writer.writerow([row[c] if row[c] is not None else "" for c in cols])
    return buf.getvalue()


# --- the command -------------------------------------------------------------


class RefundTarget(commands.Target):
    payment_id: int = Field(gt=0)
    organization_id: int = Field(gt=0)
    amount_minor: int = Field(gt=0)


async def _eligible(session: AsyncSession, t: RefundTarget) -> str | None:
    p = await session.get(PaymentModel, t.payment_id)
    if p is None or p.organization_id != t.organization_id:
        return "There is no such payment for that account."
    if p.status != "paid":
        return "Only a paid payment can be refunded."
    if provider_for(p) is None:
        return provider_state().get("reason") or "Refunds need setup."
    left = await eligible_minor(session, p)
    if t.amount_minor > left:
        return (
            f"That is more than can be refunded ({left} {p.currency} minor units left)."
        )
    return None


async def _preview(session: AsyncSession, t: RefundTarget) -> dict:
    p = await session.get(PaymentModel, t.payment_id)
    org = await session.get(OrganizationModel, p.organization_id)
    return {
        "customer": {
            "organization_id": p.organization_id,
            "name": (org.name if org else None),
        },
        "payment": {
            "id": p.id,
            "ref": _mask_ref(p.payment_id),
            "provider": p.provider,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        },
        "currency": p.currency,
        "collected_minor": collected_minor(p),
        "eligible_minor": await eligible_minor(session, p),
        "amount_minor": t.amount_minor,
        "approvers": "A second finance person.",
        "credit_balance": "Not changed by the refund; adjust from the account if the finance rule says so.",
    }


async def _handler(
    session: AsyncSession, t: RefundTarget, actor: commands.Actor
) -> commands.Outcome:
    p = await session.get(PaymentModel, t.payment_id, with_for_update=True)
    if p is None or p.organization_id != t.organization_id or p.status != "paid":
        return commands.Outcome(
            {"message": "The payment is no longer refundable."},
            state=commands.FAILED,
            reason_code="not_refundable",
        )
    left = collected_minor(p) - await _held(
        session, p.id, exclude_command=actor.command_id
    )
    if t.amount_minor > left:
        return commands.Outcome(
            {"message": f"Only {max(0, left)} left to refund."},
            state=commands.FAILED,
            reason_code="exceeds_eligible",
        )
    provider = provider_for(p)
    if provider is None:
        return commands.Outcome(
            {"message": "Refunds need setup."},
            state=commands.FAILED,
            reason_code="needs_setup",
        )
    refund = (
        await session.execute(
            select(StaffRefundModel).where(
                StaffRefundModel.command_id == actor.command_id
            )
        )
    ).scalar_one_or_none()
    if refund is None:
        refund = StaffRefundModel(
            payment_id=p.id,
            organization_id=p.organization_id,
            amount_minor=t.amount_minor,
            currency=p.currency,
            state=REQUESTED,
            provider=provider.name,
            command_id=actor.command_id,
        )
        session.add(refund)
        await session.flush()
    elif refund.state != REQUESTED:
        return commands.Outcome(
            {"refund": _refund_view(refund), "message": "Already sent; not sent again."}
        )
    command = await session.get(commands.StaffCommandModel, actor.command_id)
    try:
        created = await provider.create(
            payment_ref=p.payment_id or "",
            amount_minor=t.amount_minor,
            currency=p.currency,
            receipt=command.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001 -- unknown, never retried
        logger.warning("Refund #{} create raised {}", refund.id, type(exc).__name__)
        refund.state = OUTCOME_UNKNOWN
        refund.reason_code = type(exc).__name__[:64]
        refund.updated_at = datetime.now(UTC)
        return commands.Outcome(
            {
                "refund": _refund_view(refund),
                "message": "We are checking whether this refund was made. Do not send it again.",
            },
            state=commands.OUTCOME_UNKNOWN,
            reason_code="provider_unanswered",
        )
    refund.provider_refund_id = created.get("id")
    refund.state = PENDING
    refund.updated_at = datetime.now(UTC)
    await _reconcile_one(session, refund, p, provider)
    return commands.Outcome({"refund": _refund_view(refund)})


async def _reconcile_one(
    session: AsyncSession,
    refund: StaffRefundModel,
    payment: PaymentModel,
    provider: RefundProvider,
) -> None:
    if not refund.provider_refund_id:
        return
    try:
        status = (
            await provider.fetch(
                payment_ref=payment.payment_id or "",
                refund_id=refund.provider_refund_id,
            )
        ).get("status")
    except Exception as exc:  # noqa: BLE001 -- stays pending; read again later
        logger.info("Refund #{} status unreadable: {}", refund.id, type(exc).__name__)
        return
    now = datetime.now(UTC)
    if status == "processed":
        refund.state = REFUNDED
        refund.reconciled_at = now
        from api.services import events

        await events.emit(
            "refund_completed",
            session=session,
            organization_id=refund.organization_id,
            properties={"currency": refund.currency},
        )
    elif status == "failed":
        refund.state = FAILED
        refund.reason_code = "provider_failed"
        refund.reconciled_at = now
    refund.updated_at = now


async def reconcile_pending() -> int:
    """Read back every pending refund (the worker's sweep)."""
    changed = 0
    async with db_client.async_session() as session:
        pending = (
            (
                await session.execute(
                    select(StaffRefundModel).where(StaffRefundModel.state == PENDING)
                )
            )
            .scalars()
            .all()
        )
        for refund in pending:
            payment = await session.get(PaymentModel, refund.payment_id)
            provider = provider_for(payment) if payment else None
            if provider is None:
                continue
            before = refund.state
            await _reconcile_one(session, refund, payment, provider)
            changed += int(refund.state != before)
        await session.commit()
    return changed


commands.register(
    commands.CommandSpec(
        name="refund.request",
        summary="Refund part or all of a payment; a second finance person approves.",
        request_capability="refunds.request",
        approve_capability="refunds.approve",
        target=RefundTarget,
        handler=_handler,
        execution="worker",
        feature="staff_refunds",
        eligible=_eligible,
        preview=_preview,
        notes=("Runs once; refunded only after the provider's status is read back.",),
    )
)
