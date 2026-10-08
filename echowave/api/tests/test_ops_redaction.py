"""Stream ops, handoff 35: nothing private leaves in analytics, logs or
error reports.

What these defend: secrets, emails and phone numbers are scrubbed from free
text; content-named and non-scalar properties never reach an event, and the
drop is visible (``_redacted``) rather than silent; failures become a fixed
vocabulary of reason codes; and the deep Sentry and log scrub runs exactly
while ``telemetry_redaction`` is on.
"""

from __future__ import annotations

import pytest

from api import constants
from api.services import features
from api.services.ops import redaction
from api.utils.sentry_scrub import scrub_event


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    features.clear_snapshot()
    monkeypatch.setattr(constants, "TELEMETRY_REDACTION_ENABLED", False)
    yield
    features.clear_snapshot()


@pytest.mark.parametrize(
    "raw,leaked",
    [
        ("postgresql+asyncpg://decibyl:hunter2pass@db:5432/x", "hunter2pass"),
        ("Authorization: Bearer abcdef1234567890xyz", "abcdef1234567890xyz"),
        ("key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV", "ABCDEFGHIJKLMNOPQRSTUV"),
        ("rzp_live_ABCD1234EFGH5678", "ABCD1234EFGH5678"),
        ("aws AKIAABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOP"),
        ("api_key=supersecretvalue99", "supersecretvalue99"),
        ('{"password": "p4ssw0rd!"}', "p4ssw0rd!"),
        ("mail asha.rao@example.com now", "asha.rao@example.com"),
        ("call +91 98450 12345 please", "98450 12345"),
        (
            "tok 0123456789abcdef0123456789abcdef01",
            "0123456789abcdef0123456789abcdef01",
        ),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_Value-1", "eyJzdWIiOiIxIn0"),
    ],
)
def test_free_text_loses_secrets_and_personal_data(raw, leaked):
    out = redaction.redact_text(raw)
    assert leaked not in out


def test_ids_operators_grep_for_survive():
    text = (
        "run 7f3c2a10-1b2c-4d5e-8f90-123456789abc failed in "
        "api/services/ops/infra_health.py for org 42"
    )
    assert redaction.redact_text(text) == text


def test_properties_keep_metadata_and_say_what_was_dropped():
    props = redaction.redact_properties(
        {
            "channel": "whatsapp",
            "duration_ms": 812,
            "status": "completed",
            "prompt": "book me a flight",
            "transcript_text": "hello hello",
            "email_body": "Dear Ravi",
            "kyc_document": "aadhaar.png",
            "card_number": "4111111111111111",
            "api_key": "sk-123",
            "items": ["a", "b"],
            "note": "write to asha@example.com",
        }
    )
    assert props["channel"] == "whatsapp"
    assert props["duration_ms"] == 812
    assert props["status"] == "completed"
    assert "asha@example.com" not in props["note"]
    for name in (
        "prompt",
        "transcript_text",
        "email_body",
        "kyc_document",
        "card_number",
        "api_key",
        "items",
    ):
        assert name not in props
        assert name in props["_redacted"]


def test_long_strings_are_capped():
    props = redaction.redact_properties({"status": "x" * 1000})
    assert len(props["status"]) <= redaction.MAX_STRING


@pytest.mark.parametrize(
    "error,code",
    [
        (TimeoutError("slow"), "timeout"),
        ("HTTP 429 Too Many Requests", "rate_limited"),
        ("401 Unauthorized: invalid api key sk-xxxx", "unauthorized"),
        ("503 Service Unavailable", "provider_unavailable"),
        ("Connection reset by peer", "network"),
        ("something nobody planned for", "other"),
        ("timeout", "timeout"),
        (None, "other"),
    ],
)
def test_reason_codes_are_a_fixed_vocabulary(error, code):
    got = redaction.reason_code(error)
    assert got == code
    assert got in redaction.REASON_CODES


def _event():
    return {
        "message": "failed for asha@example.com with sk-abcdefghijklmnopqrstuv",
        "exception": {
            "values": [
                {
                    "value": "Bearer abcdefghijklmnop1234 rejected",
                    "stacktrace": {"frames": [{"vars": {"prompt": "secret plans"}}]},
                }
            ]
        },
        "breadcrumbs": {
            "values": [{"message": "+91 98450 12345", "data": {"body": "x"}}]
        },
        "extra": {"transcript": "hello", "status": "failed"},
        "request": {"data": "body", "headers": {"Authorization": "Bearer x"}},
    }


def test_sentry_baseline_scrub_runs_without_the_flag():
    event = scrub_event(_event())
    assert event["request"]["data"] == "[redacted]"
    # The deep scrub is off: the message is untouched.
    assert "asha@example.com" in event["message"]


def test_sentry_deep_scrub_runs_with_the_flag(monkeypatch):
    monkeypatch.setattr(constants, "TELEMETRY_REDACTION_ENABLED", True)
    event = scrub_event(_event())
    flat = repr(event)
    for leaked in (
        "asha@example.com",
        "abcdefghijklmnopqrstuv",
        "abcdefghijklmnop1234",
        "98450 12345",
        "secret plans",
        "hello",
    ):
        assert leaked not in flat
    assert event["extra"]["status"] == "failed"


def test_log_patcher_follows_the_flag(monkeypatch):
    record = {"message": "key=supersecretvalue99"}
    redaction.redact_record(record)
    assert "supersecretvalue99" in record["message"]
    monkeypatch.setattr(constants, "TELEMETRY_REDACTION_ENABLED", True)
    redaction.redact_record(record)
    assert "supersecretvalue99" not in record["message"]
