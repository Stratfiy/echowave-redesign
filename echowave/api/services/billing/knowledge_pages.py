"""Knowledge-base caps in pages, and the price of a page past the cap.

KAN-57. A plan includes a number of pages (``plan_limits`` ``knowledge_pages``:
50 / 500 / 2,000 / 10,000 / 50,000). Past the cap, a typed page is a tenth of
a credit — one credit per ten pages, rounded up per document — and a scanned
page, which goes through OCR, is two credits: Sarvam Document AI is ₹0.50 a
page, so one credit would lose ₹0.02 on every scan. The bytes ceiling on a
plan stays as the engineering limit on what the worker can hold; pages are
what is sold.

Query-time embedding is inside the two-credit knowledge answer (KAN-56);
re-embedding on a model change is free. Nothing here charges for a page
inside the cap.

The cap is on what is *held*: deleting a document gives its pages back, the
same way the bytes ceiling works. A document is settled once, when
processing completes and its page count is known, keyed on the document so
a re-run of the job charges nothing twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CreditLedgerModel
from api.enums import CreditLedgerKind
from api.services.billing.credits import PAISE_PER_CREDIT

CAP_KEY = "knowledge_pages"
REF_TYPE = "knowledge_pages"

#: Typed pages past the cap: one credit buys ten.
TYPED_PAGES_PER_CREDIT = 10
#: A scanned page past the cap, recognised by OCR.
SCANNED_PAGE_CREDITS = 2

#: A document with no page structure (a .docx, a .txt, a web page) is
#: counted at this many characters a page — an engineering figure, close to
#: a typed A4 page, so the cap means the same thing whatever the format.
CHARS_PER_PAGE = 3_000

#: The extractor name the OCR path records on a document's metadata.
OCR_EXTRACTOR = "tesseract"


@dataclass(frozen=True)
class Pages:
    total: int
    scanned: int

    @property
    def typed(self) -> int:
        return max(0, self.total - self.scanned)


def pages_from_metadata(metadata: dict | None) -> Pages:
    """How many pages a processed document is, and how many were scanned.

    A PDF reports its page count; a scan reports how many pages OCR read;
    anything else is measured by characters. Never fewer than one page for
    a document that produced text, so an empty-looking upload still counts.
    """
    meta = metadata or {}
    scanned = (
        int(meta.get("ocr_pages") or 0) if meta.get("extractor") == OCR_EXTRACTOR else 0
    )
    total = int(meta.get("page_count") or 0)
    if total <= 0:
        chars = int(meta.get("character_count") or 0)
        total = -(-chars // CHARS_PER_PAGE) if chars > 0 else 0
    total = max(total, scanned, 1)
    return Pages(total=total, scanned=min(scanned, total))


def overage_credits(*, typed_pages: int, scanned_pages: int) -> int:
    """Credits for pages past the cap: ceil(typed / 10) + 2 × scanned."""
    typed = max(0, int(typed_pages))
    scanned = max(0, int(scanned_pages))
    return -(-typed // TYPED_PAGES_PER_CREDIT) + scanned * SCANNED_PAGE_CREDITS


@dataclass(frozen=True)
class Quote:
    """What an upload of ``pages`` would cost this account, before it runs."""

    pages_used: int
    #: None is unlimited.
    pages_cap: int | None
    #: The rung with a larger cap, or None (then: support).
    raise_to: str | None
    pages: Pages
    charged_typed: int
    charged_scanned: int

    @property
    def over_cap(self) -> bool:
        return self.pages_cap is not None and self.pages_used >= self.pages_cap

    @property
    def credits(self) -> int:
        return overage_credits(
            typed_pages=self.charged_typed, scanned_pages=self.charged_scanned
        )

    @property
    def raise_path(self) -> str:
        return f"upgrade:{self.raise_to}" if self.raise_to else "support"

    def as_dict(self) -> dict:
        return {
            "pages_used": self.pages_used,
            "pages_cap": self.pages_cap,
            "over_cap": self.over_cap,
            "raise_to": self.raise_to,
            "raise_path": self.raise_path,
            "pages": self.pages.total,
            "scanned_pages": self.pages.scanned,
            "charged_typed_pages": self.charged_typed,
            "charged_scanned_pages": self.charged_scanned,
            "credits": self.credits,
            "typed_pages_per_credit": TYPED_PAGES_PER_CREDIT,
            "scanned_page_credits": SCANNED_PAGE_CREDITS,
        }


def split_past_the_cap(*, used: int, cap: int | None, pages: Pages) -> tuple[int, int]:
    """(typed, scanned) pages of this document that fall past the cap.

    The included room goes to typed pages first, so a scan is what a
    customer at the edge of their cap pays for — the dearer page is the one
    the plan does not absorb.
    """
    if cap is None:
        return 0, 0
    room = max(0, cap - used)
    typed_included = min(pages.typed, room)
    room -= typed_included
    scanned_included = min(pages.scanned, room)
    return pages.typed - typed_included, pages.scanned - scanned_included


async def pages_used(
    session: AsyncSession,
    *,
    organization_id: int,
    excluding_document_id: int | None = None,
) -> int:
    """Pages this account holds: active, completed documents."""
    from api.db.models import KnowledgeBaseDocumentModel as Doc

    query = select(func.coalesce(func.sum(Doc.page_count), 0)).where(
        Doc.organization_id == organization_id,
        Doc.is_active == True,  # noqa: E712
        Doc.processing_status == "completed",
    )
    if excluding_document_id is not None:
        query = query.where(Doc.id != excluding_document_id)
    return int(await session.scalar(query) or 0)


async def quote(
    session: AsyncSession,
    *,
    organization_id: int,
    pages: Pages,
    excluding_document_id: int | None = None,
) -> Quote:
    from api.services.billing import plan_limits

    limit = await plan_limits.limit_for_organization(
        session, organization_id=organization_id, key=CAP_KEY
    )
    used = await pages_used(
        session,
        organization_id=organization_id,
        excluding_document_id=excluding_document_id,
    )
    typed, scanned = split_past_the_cap(used=used, cap=limit.value, pages=pages)
    return Quote(
        pages_used=used,
        pages_cap=limit.value,
        raise_to=limit.raise_to,
        pages=pages,
        charged_typed=typed,
        charged_scanned=scanned,
    )


async def _balance_paise(session: AsyncSession, *, organization_id: int) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
                CreditLedgerModel.organization_id == organization_id
            )
        )
        or 0
    )


async def settle(
    session: AsyncSession,
    *,
    organization_id: int,
    document_id: int,
    pages: Pages,
    filename: str = "",
) -> int:
    """Charge the pages of one processed document that fall past the cap.
    Returns the paise debited (0 inside the cap, already settled, or an
    internal account)."""
    from api.services.billing.internal_accounts import is_internal

    if await is_internal(session, organization_id):
        return 0
    existing = await session.scalar(
        select(CreditLedgerModel.id).where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.USAGE.value,
            CreditLedgerModel.ref_type == REF_TYPE,
            CreditLedgerModel.ref_id == str(document_id),
        )
    )
    if existing is not None:
        return 0
    q = await quote(
        session,
        organization_id=organization_id,
        pages=pages,
        excluding_document_id=document_id,
    )
    credits = q.credits
    if credits <= 0:
        return 0
    amount = credits * PAISE_PER_CREDIT
    balance = await _balance_paise(session, organization_id=organization_id)
    parts = []
    if q.charged_typed:
        parts.append(
            f"{q.charged_typed} typed page{'s' if q.charged_typed != 1 else ''}"
        )
    if q.charged_scanned:
        parts.append(
            f"{q.charged_scanned} scanned page{'s' if q.charged_scanned != 1 else ''}"
        )
    session.add(
        CreditLedgerModel(
            organization_id=organization_id,
            delta_paise=-amount,
            kind=CreditLedgerKind.USAGE.value,
            ref_type=REF_TYPE,
            ref_id=str(document_id),
            balance_after_paise=balance - amount,
            note=(
                f"Knowledge pages past the plan's {q.pages_cap:,}: "
                f"{', '.join(parts)} · {credits} credit{'s' if credits != 1 else ''}"
                + (f" · {filename[:80]}" if filename else "")
            ),
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return amount


async def settle_in_own_session(
    *, organization_id: int, document_id: int, pages: Pages, filename: str = ""
) -> int:
    """``settle`` from the processing worker. Never raises: a document that
    was processed stays processed, and a failed charge is logged loudly."""
    try:
        from api.db import db_client

        async with db_client.async_session() as session:
            amount = await settle(
                session,
                organization_id=organization_id,
                document_id=document_id,
                pages=pages,
                filename=filename,
            )
            await session.commit()
            return amount
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.error(
            "Could not settle knowledge pages for document {} (org {}): {}",
            document_id,
            organization_id,
            exc,
        )
        return 0
