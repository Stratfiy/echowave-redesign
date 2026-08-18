"""Emailing a tax document, and finding somebody to email it to.

Split out of ``tasks/email_tax_document.py`` because two callers need it: the
job that runs when a document is issued, and the route that resends one on
request. The job is fire-and-forget and the route has to report what happened,
so the shared part is the part that does the work and reports it.

**The recipient is resolved twice over, and the order matters.** Each document
carries a ``customer_snapshot`` — the billing profile exactly as it stood when
the document was issued, which is what makes a reissued PDF match the original.
For sending, that snapshot is the right first choice: it is the address the
customer had given us at the time.

But a snapshot taken before the customer filled in their profile has no address
in it at all, and that document is then undeliverable forever — which is what
happened on the live deployment: four receipt vouchers were issued to an
account that had not completed its billing profile, so each one logged "no
billing email on file" and was never sent to anybody. The documents were
correct and downloadable; the customer simply never received them.

So the live profile is the fallback. It is not used to *rewrite* the document —
the snapshot still governs what the PDF says — only to answer "who do we send
this to", which is a question about now rather than about then.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import BillingProfileModel, TaxDocumentModel
from api.services.messaging.email import SendResult, email_is_configured, send_email

_KIND_TITLES = {
    "receipt_voucher": "Receipt Voucher",
    "tax_invoice": "Tax Invoice",
}


async def resolve_recipient(
    session: AsyncSession, document: TaxDocumentModel
) -> str | None:
    """Who this document should go to: the snapshot first, then the profile."""
    snapshot_email = (document.customer_snapshot or {}).get("billing_email")
    if snapshot_email and snapshot_email.strip():
        return snapshot_email.strip()

    current = await session.scalar(
        select(BillingProfileModel.billing_email).where(
            BillingProfileModel.organization_id == document.organization_id
        )
    )
    return current.strip() if current and current.strip() else None


async def send_document(
    document: TaxDocumentModel, *, recipient: str, pdf_bytes: bytes
) -> SendResult:
    """Send one document's PDF. Never raises — see ``send_email``."""
    title = _KIND_TITLES.get(document.kind, document.kind)
    result = await send_email(
        sender="billing",
        to=recipient,
        subject=f"{title} {document.number}",
        body_text=(
            f"Your {title.lower()} {document.number} is attached.\n\n"
            f"Total: Rs. {int(document.total_paise) / 100:,.2f}\n\n"
            "This is an automated message from Decibyl."
        ),
        attachment_bytes=pdf_bytes,
        attachment_filename=f"{document.number.replace('/', '-')}.pdf",
    )
    if not result.ok:
        logger.warning(
            "Could not email document {} to {}: {}",
            document.number,
            recipient,
            result.error,
        )
    return result


def render_pdf(document: TaxDocumentModel) -> bytes:
    """Render a document, importing reportlab only when one is actually wanted.

    Deferred deliberately. This module is reachable from ``tasks/arq.py``,
    which ``routes/campaign.py`` imports, which puts it on the import path of
    the entire API. ``document_pdf`` builds its styles from reportlab at import
    time, so a module-level import here would let a broken or missing PDF
    library stop the API from booting at all — telephony included. Deferring it
    keeps a PDF problem a PDF problem.
    """
    from api.services.billing.document_pdf import render_document_pdf

    return render_document_pdf(document)


__all__ = [
    "email_is_configured",
    "render_pdf",
    "resolve_recipient",
    "send_document",
]
