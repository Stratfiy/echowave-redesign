"""Outbound email over SMTP.

Unconfigured is a normal state (no SMTP_HOST set), so the important behaviour
is that a send is skipped and reported rather than raising -- the same posture
as every other optional integration in this codebase. When configured, the
right calls should go to `smtplib`, which we replace with a fake so no test
touches a real mail server.
"""

from __future__ import annotations

import pytest

from api.services.messaging import email as email_module


class _FakeSmtp:
    """Stands in for smtplib.SMTP / SMTP_SSL as a context manager."""

    sent: list = []
    started_tls = False
    logged_in_with: tuple | None = None
    raise_on_send: Exception | None = None

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        type(self).started_tls = True

    def login(self, username, password):
        type(self).logged_in_with = (username, password)

    def send_message(self, message):
        if type(self).raise_on_send is not None:
            raise type(self).raise_on_send
        type(self).sent.append(message)


@pytest.fixture(autouse=True)
def _reset_fake_smtp():
    _FakeSmtp.sent = []
    _FakeSmtp.started_tls = False
    _FakeSmtp.logged_in_with = None
    _FakeSmtp.raise_on_send = None
    yield


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(email_module, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_module, "SMTP_PORT", 587)
    monkeypatch.setattr(email_module, "SMTP_USERNAME", "apikey")
    monkeypatch.setattr(email_module, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(email_module, "SMTP_USE_TLS", True)
    monkeypatch.setattr(email_module, "EMAIL_FROM_ADDRESS", "billing@decibyl.ai")
    monkeypatch.setattr(email_module, "EMAIL_FROM_NAME", "Decibyl")
    monkeypatch.setattr(email_module.smtplib, "SMTP", _FakeSmtp)
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", _FakeSmtp)


@pytest.mark.asyncio
class TestSendEmail:
    async def test_unconfigured_skips_the_send_and_reports_why(self, monkeypatch):
        monkeypatch.setattr(email_module, "SMTP_HOST", None)
        monkeypatch.setattr(email_module, "EMAIL_FROM_ADDRESS", None)

        result = await email_module.send_email(
            to="customer@example.com", subject="Hi", body_text="Body"
        )

        assert result.ok is False
        assert _FakeSmtp.sent == []

    async def test_a_configured_send_reaches_smtp_with_starttls_and_login(
        self, configured
    ):
        result = await email_module.send_email(
            to="customer@example.com",
            subject="Receipt Voucher RV/25-26/000001",
            body_text="Attached.",
            attachment_bytes=b"%PDF-fake",
            attachment_filename="RV-25-26-000001.pdf",
        )

        assert result.ok is True
        assert len(_FakeSmtp.sent) == 1
        message = _FakeSmtp.sent[0]
        assert message["To"] == "customer@example.com"
        assert message["Subject"] == "Receipt Voucher RV/25-26/000001"
        assert _FakeSmtp.started_tls is True
        assert _FakeSmtp.logged_in_with == ("apikey", "secret")

        attachments = list(message.iter_attachments())
        assert len(attachments) == 1
        assert attachments[0].get_content_type() == "application/pdf"

    async def test_a_send_with_no_attachment_still_sends(self, configured):
        result = await email_module.send_email(
            to="customer@example.com", subject="Low balance", body_text="Top up soon."
        )
        assert result.ok is True
        assert list(_FakeSmtp.sent[0].iter_attachments()) == []

    async def test_an_smtp_failure_is_reported_not_raised(self, configured):
        _FakeSmtp.raise_on_send = ConnectionRefusedError("refused")

        result = await email_module.send_email(
            to="customer@example.com", subject="Hi", body_text="Body"
        )

        assert result.ok is False
        assert "refused" in (result.error or "")


def test_email_is_configured_requires_both_host_and_from_address(monkeypatch):
    monkeypatch.setattr(email_module, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_module, "EMAIL_FROM_ADDRESS", None)
    assert email_module.email_is_configured() is False

    monkeypatch.setattr(email_module, "EMAIL_FROM_ADDRESS", "billing@decibyl.ai")
    assert email_module.email_is_configured() is True


class TestTheTemplateDocumentsAWorkingCombination:
    """The example in decibyl.env.template has to actually send.

    It shipped with ``SMTP_PORT=587`` and ``SMTP_USE_TLS=false``, above a
    comment asserting that 587 "negotiates STARTTLS" on its own. It does not:
    ``_send_sync`` issues STARTTLS only when ``SMTP_USE_TLS`` is true, so that
    pair opens a plaintext session and every hosted provider refuses it. Resend
    answers ``538 Must issue a STARTTLS command first``, which reads like a bad
    credential and sends you looking in the wrong place.

    An operator copying the documented example must end up with a connection
    that is encrypted. Asserted against the template file rather than a
    docstring, because the file is what people copy.
    """

    @staticmethod
    def _template_smtp_settings() -> dict[str, str]:
        from pathlib import Path

        template = (
            Path(__file__).resolve().parents[2] / "deploy" / "decibyl.env.template"
        )
        settings: dict[str, str] = {}
        for raw in template.read_text().splitlines():
            line = raw.lstrip("#").strip()
            if line.startswith("SMTP_") and "=" in line:
                key, _, value = line.partition("=")
                settings[key.strip()] = value.strip()
        return settings

    def test_the_example_names_a_port_and_a_tls_setting(self):
        settings = self._template_smtp_settings()
        assert "SMTP_PORT" in settings, "the template stopped documenting a port"
        assert "SMTP_USE_TLS" in settings

    def test_the_documented_pair_would_encrypt_the_connection(self):
        """Mirrors the branch in ``_send_sync``: 465 gets implicit TLS, every
        other port needs the flag on for STARTTLS to be issued at all."""
        settings = self._template_smtp_settings()
        port = int(settings["SMTP_PORT"])
        use_tls = settings["SMTP_USE_TLS"].lower() == "true"

        implicit_tls = use_tls and port == 465
        starttls = use_tls and port != 465

        assert implicit_tls or starttls, (
            f"the template documents SMTP_PORT={port} with "
            f"SMTP_USE_TLS={settings['SMTP_USE_TLS']}, which sends in "
            "plaintext and is refused by every hosted SMTP provider"
        )
