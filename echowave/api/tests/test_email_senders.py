"""Which address a message goes out as.

Receipts and security alerts are different conversations, and the from address
is what a customer filters their inbox on — one they keep, one they act on.
Everything went out as a single `EMAIL_FROM_ADDRESS`.

The fallback matters as much as the split. A deployment that configures only
the one address must keep behaving exactly as it did, because most of them do.
"""

from __future__ import annotations

import pytest

from api.services.messaging import email as email_service


class TestChoosingTheAddress:
    def test_money_goes_out_as_billing(self, monkeypatch):
        monkeypatch.setitem(email_service.SENDERS, "billing", "billing@decibyl.ai")

        assert email_service.sender_address("billing") == "billing@decibyl.ai"

    def test_everything_else_goes_out_as_notifications(self, monkeypatch):
        monkeypatch.setitem(
            email_service.SENDERS, "notifications", "notifications@decibyl.ai"
        )

        assert (
            email_service.sender_address("notifications") == "notifications@decibyl.ai"
        )

    def test_an_unset_purpose_falls_back(self, monkeypatch):
        """A deployment with only EMAIL_FROM_ADDRESS keeps working. Most of
        them are that deployment."""
        monkeypatch.setitem(email_service.SENDERS, "billing", None)
        monkeypatch.setattr(email_service, "EMAIL_FROM_ADDRESS", "hello@decibyl.ai")

        assert email_service.sender_address("billing") == "hello@decibyl.ai"

    def test_an_unknown_purpose_falls_back_rather_than_raising(self, monkeypatch):
        """A typo in a caller must not stop a receipt being delivered."""
        monkeypatch.setattr(email_service, "EMAIL_FROM_ADDRESS", "hello@decibyl.ai")

        assert email_service.sender_address("invoices") == "hello@decibyl.ai"
        assert email_service.sender_address(None) == "hello@decibyl.ai"


@pytest.mark.asyncio
class TestTheAddressReachesTheMessage:
    async def test_the_from_header_uses_the_chosen_sender(self, monkeypatch):
        """The value being right is no use if the message is built from a
        different one."""
        seen = {}

        def _capture(**kwargs):
            seen.update(kwargs)

        monkeypatch.setattr(email_service, "SMTP_HOST", "smtp.resend.com")
        monkeypatch.setattr(email_service, "EMAIL_FROM_ADDRESS", "hello@decibyl.ai")
        monkeypatch.setitem(email_service.SENDERS, "billing", "billing@decibyl.ai")
        monkeypatch.setattr(email_service, "_send_sync", _capture)

        result = await email_service.send_email(
            to="customer@example.com",
            subject="Receipt",
            body_text="Thanks",
            sender="billing",
        )

        assert result.ok
        assert seen["from_address"] == "billing@decibyl.ai"

    async def test_the_default_purpose_is_notifications(self, monkeypatch):
        """Callers that say nothing are not sending invoices."""
        seen = {}

        monkeypatch.setattr(email_service, "SMTP_HOST", "smtp.resend.com")
        monkeypatch.setattr(email_service, "EMAIL_FROM_ADDRESS", "hello@decibyl.ai")
        monkeypatch.setitem(
            email_service.SENDERS, "notifications", "notifications@decibyl.ai"
        )
        monkeypatch.setattr(email_service, "_send_sync", lambda **kw: seen.update(kw))

        await email_service.send_email(
            to="customer@example.com", subject="Hello", body_text="Hi"
        )

        assert seen["from_address"] == "notifications@decibyl.ai"
