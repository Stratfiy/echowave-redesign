"""Debit the credit ledger for the vendor cost of embedding a document.

Document upload (``tasks/knowledge_base_processing.py``) embeds every chunk on
our key, and until now nothing in this codebase priced that — not even as an
``uncosted`` line, because ``cost_engine.compute_call_cost`` is built around a
call receipt and ingestion has no ``workflow_run_id`` to attach one to. This is
the other half of embedding billing; query-time retrieval during a call is
already priced through the ordinary call-costing path (see ``usage.py`` and
``cost_engine.py``).

Shaped like ``rentals.py``, not like ``costing.py``: a direct ledger debit
outside the call-costing path, because this event is not a call either. Kept
as its own module rather than folded into either of those for the same
reason ``rentals.py`` is its own module — a different-shaped billing event
deserves its own home, not a branch in code built around a different shape.

**Never a standalone customer-facing line.** The founder's instruction
(2026-08-28, see ``PRICING-DECISIONS.md`` and ``BUILD-PLAN.md`` Phase 3) is to
meter every internal cost for real but combine what a customer sees into a
handful of buckets. This module writes the real, itemised debit to the
ledger and the internal unit-economics screen; nothing here renders a
customer-facing "embedding ingestion: ₹X" row, and nothing calling it should
either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CreditLedgerModel, EmbeddingIngestionCostModel
from api.enums import CostComponent, CreditLedgerKind
from api.services.billing.markup import resolve_markup_bps, resolve_markup_override_bps
from api.services.billing.money import cost_paise, round_half_up_div
from api.services.billing.rates import resolve_provider_rate

#: The ref_type every ledger row this module writes is keyed on, alongside
#: organization_id + the document's own id — mirrors how a call debit is keyed
#: on ("workflow_run", workflow_run_id) in costing.py.
REF_TYPE = "kb_document"


@dataclass(frozen=True)
class IngestionPrice:
    """What embedding a document's chunks cost, and what it was priced at."""

    #: What the vendor charged us, before markup.
    vendor_cost_paise: int
    #: What this would debit the account for, vendor cost with the managed
    #: markup applied. Never itself shown to the customer as a line — see the
    #: module docstring.
    charged_paise: int


async def _current_balance_paise(session: AsyncSession, *, organization_id: int) -> int:
    """Balance derived from the ledger. Never read from a cached column.

    A local copy rather than an import from ``costing.py``/``rentals.py``: the
    same three-line query is duplicated in both of those already, and each
    copy exists to avoid a cross-module import between siblings that would
    otherwise have no other reason to depend on each other.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
            CreditLedgerModel.organization_id == organization_id
        )
    )
    return int(total or 0)


async def estimate_ingestion_cost_paise(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    tokens: int,
    at: datetime | None = None,
) -> IngestionPrice | None:
    """What embedding ``tokens`` tokens on ``provider``/``model`` would cost.

    ``None`` when there is no provider rate on file for this provider/model —
    the same "uncosted, not free" rule ``compute_call_cost`` follows for a
    call. The caller decides what that means here: ``debit_ingestion_cost``
    lets the document through unbilled rather than refusing a customer's
    upload over a rate-card gap that is ours to fix, not theirs to wait on.
    """
    at = at or datetime.now(UTC)
    if tokens <= 0:
        return IngestionPrice(vendor_cost_paise=0, charged_paise=0)

    resolved = await resolve_provider_rate(
        session,
        provider=provider,
        component=CostComponent.EMBEDDING,
        at=at,
        model=model,
    )
    if resolved is None:
        return None

    vendor_cost = cost_paise(
        quantity=tokens, rate_mpaise=resolved.rate_mpaise, unit=resolved.unit
    )

    # Most-specific-first, same rule compute_call_cost applies per line: a
    # per-model override beats the blanket managed markup.
    override_bps = await resolve_markup_override_bps(
        session,
        provider=provider,
        component=CostComponent.EMBEDDING,
        at=at,
        model=model,
    )
    markup_bps = (
        override_bps
        if override_bps is not None
        else await resolve_markup_bps(session, at=at)
    )

    charged = round_half_up_div(vendor_cost * markup_bps, 10_000)
    return IngestionPrice(vendor_cost_paise=vendor_cost, charged_paise=charged)


async def debit_ingestion_cost(
    session: AsyncSession,
    *,
    organization_id: int,
    document_id: int,
    provider: str,
    model: str,
    tokens: int,
    at: datetime | None = None,
) -> IngestionPrice | None:
    """Debit the ledger for what embedding a document's chunks actually cost.

    Writes two rows in the same transaction: the ``credit_ledger`` debit (what
    the customer paid) and an ``embedding_ingestion_costs`` row alongside it
    (what the vendor charged us) — the same charge/cost pairing
    ``call_cost_items`` keeps for every call, so a margin query has both
    halves rather than only the bill.

    Exactly once per document — checked here, and backed at the database
    level by ``uq_credit_ledger_embedding_ingest_ref`` /
    ``uq_embedding_ingestion_costs_document``, the same
    check-then-insert-with-a-partial-unique-index-backstop shape
    ``costing.py:_debit_ledger`` uses for a call receipt. A retried ingestion
    (the ARQ job re-run after a crash between the vendor call and this write)
    finds the row already there and debits nothing a second time.

    Returns ``None`` — and debits nothing — when there is no rate on file for
    this provider/model, or when the document is already debited. Returns an
    ``IngestionPrice`` of zero without writing a row when ``tokens`` is zero
    or the resolved charge rounds to nothing; a zero-paise ledger row is noise
    a statement never needs.
    """
    at = at or datetime.now(UTC)
    if tokens <= 0:
        return IngestionPrice(vendor_cost_paise=0, charged_paise=0)

    ref_id = str(document_id)
    existing = await session.scalar(
        select(CreditLedgerModel).where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.EMBEDDING_INGEST.value,
            CreditLedgerModel.ref_type == REF_TYPE,
            CreditLedgerModel.ref_id == ref_id,
        )
    )
    if existing is not None:
        logger.debug(
            "Document {} already debited for ingestion embeddings", document_id
        )
        return None

    price = await estimate_ingestion_cost_paise(
        session, provider=provider, model=model, tokens=tokens, at=at
    )
    if price is None:
        logger.warning(
            "No rate on file for embedding {}/{}; document {} ingested {} "
            "tokens unbilled",
            provider,
            model,
            document_id,
            tokens,
        )
        return None
    if price.charged_paise <= 0:
        return price

    balance = await _current_balance_paise(session, organization_id=organization_id)
    session.add(
        CreditLedgerModel(
            organization_id=organization_id,
            delta_paise=-price.charged_paise,
            kind=CreditLedgerKind.EMBEDDING_INGEST.value,
            ref_type=REF_TYPE,
            ref_id=ref_id,
            balance_after_paise=balance - price.charged_paise,
            note=f"Knowledge-base ingestion embeddings ({tokens} tokens, {provider}/{model})",
        )
    )
    # The ledger row above is what the customer paid; this is the other half
    # of the pair call_cost_items keeps for every call -- what the vendor
    # charged us. Without it, a margin query has nothing to compare the
    # charge against. Same key as the ledger row, so the two can never drift
    # into recording different documents.
    session.add(
        EmbeddingIngestionCostModel(
            organization_id=organization_id,
            document_id=document_id,
            provider=provider,
            model=model,
            tokens=tokens,
            vendor_cost_paise=price.vendor_cost_paise,
            charged_paise=price.charged_paise,
        )
    )
    return price


async def has_balance_for_estimate(
    session: AsyncSession, *, organization_id: int, estimate: IngestionPrice | None
) -> bool:
    """Whether the account can afford an estimated ingestion charge.

    Called before the vendor is paid, not after — the money-losing direction
    is calling the vendor and then finding out the account cannot be charged
    for it. ``estimate is None`` (no rate on file) is treated as affordable:
    an unpriced model should not block an upload any more than
    ``debit_ingestion_cost`` refuses to bill one, for the same reason.
    """
    if estimate is None or estimate.charged_paise <= 0:
        return True
    balance = await _current_balance_paise(session, organization_id=organization_id)
    return balance >= estimate.charged_paise
