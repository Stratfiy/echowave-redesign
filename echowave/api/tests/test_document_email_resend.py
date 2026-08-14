"""Getting a tax document to the customer who paid for it.

Four receipt vouchers were issued on the live deployment to an account that had
not yet completed its billing profile. Each one was correct, numbered, and
downloadable — and each logged "no billing email on file" and was sent to
nobody. Completing the profile afterwards changed nothing: the send happens
once, at issue, and there was no way to ask for it again.

Two halves to the fix, and both are pinned here:

* the recipient falls back to the live billing profile when the document's own
  snapshot has none, so an address added later is usable;
* a document can be sent again on request, without being reissued.

The document itself is never rewritten. Its snapshot is what the PDF says, and
a resend is a delivery, not a new document.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.billing import document_email
from api.services.messaging.email import SendResult


def _document(**overrides):
    defaults = dict(
        id=1,
        organization_id=30,
        kind="receipt_voucher",
        number="RV/26-27/000001",
        total_paise=59000,
        customer_snapshot={},
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestResolveRecipient:
    """The snapshot is the record; the profile is the fallback."""

    async def test_the_snapshot_address_wins(self):
        session = AsyncMock()
        document = _document(customer_snapshot={"billing_email": "then@example.com"})

        assert (
            await document_email.resolve_recipient(session, document)
            == "then@example.com"
        )
        session.scalar.assert_not_awaited()

    async def test_the_live_profile_is_used_when_the_snapshot_has_none(self):
        """The production case: issued before the profile was filled in."""
        session = AsyncMock()
        session.scalar.return_value = "now@example.com"

        assert (
            await document_email.resolve_recipient(session, _document())
            == "now@example.com"
        )

    async def test_a_blank_snapshot_address_is_not_an_address(self):
        session = AsyncMock()
        session.scalar.return_value = "now@example.com"
        document = _document(customer_snapshot={"billing_email": "   "})

        assert (
            await document_email.resolve_recipient(session, document)
            == "now@example.com"
        )

    async def test_nothing_anywhere_is_none_rather_than_empty_string(self):
        """Callers branch on this, so it must be falsey and unambiguous."""
        session = AsyncMock()
        session.scalar.return_value = None

        assert await document_email.resolve_recipient(session, _document()) is None

    async def test_a_blank_profile_address_is_also_none(self):
        session = AsyncMock()
        session.scalar.return_value = "  "

        assert await document_email.resolve_recipient(session, _document()) is None


class TestSendDocument:
    async def test_it_attaches_the_pdf_under_a_filename_a_filesystem_accepts(self):
        """Document numbers contain slashes; filenames cannot."""
        with patch.object(
            document_email,
            "send_email",
            new=AsyncMock(return_value=SendResult(ok=True)),
        ) as sender:
            await document_email.send_document(
                _document(), recipient="to@example.com", pdf_bytes=b"%PDF-1.4"
            )

        kwargs = sender.await_args.kwargs
        assert kwargs["to"] == "to@example.com"
        assert kwargs["attachment_bytes"] == b"%PDF-1.4"
        assert "/" not in kwargs["attachment_filename"]
        assert kwargs["attachment_filename"] == "RV-26-27-000001.pdf"

    async def test_the_subject_names_the_document(self):
        with patch.object(
            document_email,
            "send_email",
            new=AsyncMock(return_value=SendResult(ok=True)),
        ) as sender:
            await document_email.send_document(
                _document(), recipient="to@example.com", pdf_bytes=b""
            )

        assert sender.await_args.kwargs["subject"] == "Receipt Voucher RV/26-27/000001"

    async def test_a_failed_send_is_returned_not_raised(self):
        """Callers decide. The job ignores it; the route reports it."""
        with patch.object(
            document_email,
            "send_email",
            new=AsyncMock(return_value=SendResult(ok=False, error="refused")),
        ):
            result = await document_email.send_document(
                _document(), recipient="to@example.com", pdf_bytes=b""
            )

        assert result.ok is False
        assert result.error == "refused"

    @pytest.mark.parametrize(
        "kind,expected",
        [
            ("receipt_voucher", "Receipt Voucher RV/26-27/000001"),
            ("tax_invoice", "Tax Invoice RV/26-27/000001"),
            ("something_new", "something_new RV/26-27/000001"),
        ],
    )
    async def test_an_unknown_kind_still_sends(self, kind, expected):
        """A new document kind must not make the send unreachable."""
        with patch.object(
            document_email,
            "send_email",
            new=AsyncMock(return_value=SendResult(ok=True)),
        ) as sender:
            await document_email.send_document(
                _document(kind=kind), recipient="to@example.com", pdf_bytes=b""
            )

        assert sender.await_args.kwargs["subject"] == expected


class TestTheRouteIsRegistered:
    """The resend has to be reachable, and only by the account that owns it.

    Asserted against the OpenAPI schema rather than app.routes: FastAPI 0.141
    no longer flattens included routers into ``app.routes``, so filtering that
    list finds nothing and the test passes for the wrong reason.
    """

    def test_the_resend_endpoint_exists(self):
        from api.app import app

        paths = app.openapi()["paths"]
        assert "/api/v1/billing/documents/{document_id}/email" in paths

    def test_it_is_a_post(self):
        from api.app import app

        spec = app.openapi()["paths"]["/api/v1/billing/documents/{document_id}/email"]
        assert set(spec) == {"post"}
