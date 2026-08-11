"""Email a tax document's PDF to the account it was issued to, on issue.

Best-effort and asynchronous: issuing the document is the part that must not
fail (it is evidence a payment or a month of usage happened), so it runs in
its own transaction and this job is enqueued afterwards rather than run
inline. A mail server timeout must never roll back a receipt voucher.

Reads the recipient off the document's own ``customer_snapshot`` rather than
re-fetching the live billing profile -- a document is a statement about a
moment, and the address it should go to is the one it was addressed to, not
wherever the account has since moved.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.db.models import TaxDocumentModel
from api.services.messaging.email import email_is_configured, send_email

_KIND_TITLES = {
    "receipt_voucher": "Receipt Voucher",
    "tax_invoice": "Tax Invoice",
}


def email_tax_document_job_id(document_id: int) -> str:
    """Deterministic job id so a duplicate issue-then-enqueue collapses to one send."""
    return f"email-tax-document-{document_id}"


async def email_tax_document(_ctx, document_id: int) -> None:
    if not email_is_configured():
        return

    async with db_client.async_session() as session:
        document = await session.get(TaxDocumentModel, document_id)
        if document is None:
            logger.warning("email_tax_document: document {} not found", document_id)
            return

        recipient = (document.customer_snapshot or {}).get("billing_email")
        if not recipient:
            logger.info(
                "Not emailing document {}: no billing email on file for org {}",
                document.number,
                document.organization_id,
            )
            return

        # Imported here, not at module scope. This task is reachable from
        # tasks/arq.py, which routes/campaign.py imports, which puts it on the
        # import path of the entire API. document_pdf builds its styles from
        # reportlab at import time, so a module-level import here means a
        # broken or missing PDF library stops the API from booting at all --
        # telephony included. Deferring it keeps a PDF problem a PDF problem.
        from api.services.billing.document_pdf import render_document_pdf

        pdf_bytes = render_document_pdf(document)

    title = _KIND_TITLES.get(document.kind, document.kind)
    result = await send_email(
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
