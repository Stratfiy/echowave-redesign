"""Email a tax document's PDF to the account it was issued to, on issue.

Best-effort and asynchronous: issuing the document is the part that must not
fail (it is evidence a payment or a month of usage happened), so it runs in
its own transaction and this job is enqueued afterwards rather than run
inline. A mail server timeout must never roll back a receipt voucher.

Prefers the recipient on the document's own ``customer_snapshot`` -- a document
is a statement about a moment, and the address it should go to is the one it
was addressed to. It falls back to the live billing profile only when the
snapshot has no address at all, which means the account had not filled its
profile in when the document was issued; see
``services/billing/document_email.py`` for why that fallback exists.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.db.models import TaxDocumentModel
from api.services.billing.document_email import (
    email_is_configured,
    render_pdf,
    resolve_recipient,
    send_document,
)


def email_tax_document_job_id(document_id: int) -> str:
    """Deterministic job id so a duplicate issue-then-enqueue collapses to one send."""
    return f"email-tax-document-{document_id}"


async def email_tax_document(_ctx, document_id: int) -> None:
    """Email a freshly issued document to the account it belongs to.

    Best-effort by design: it runs after the document is already committed, so
    every failure here is "nothing was sent", never "the document is gone".
    """
    if not email_is_configured():
        return

    async with db_client.async_session() as session:
        document = await session.get(TaxDocumentModel, document_id)
        if document is None:
            logger.warning("email_tax_document: document {} not found", document_id)
            return

        recipient = await resolve_recipient(session, document)
        if not recipient:
            logger.info(
                "Not emailing document {}: no billing email on file for org {}. "
                "It stays downloadable, and can be sent once a billing email "
                "exists.",
                document.number,
                document.organization_id,
            )
            return

        pdf_bytes = render_pdf(document)

    await send_document(document, recipient=recipient, pdf_bytes=pdf_bytes)
