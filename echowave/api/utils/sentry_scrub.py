"""Keep personal data out of Sentry.

An error report is useful for its stack trace, not for who was on the other
end. Request bodies carry phone numbers, transcripts and passwords; cookies and
the Authorization header carry sessions; the client IP is personal data under
the DPDP Act and GDPR. None of it helps fix a bug, and every copy in a third
party's store is one more place an erasure request cannot reach.

`send_default_pii=False` stops the SDK attaching the user's IP and cookies by
default. This hook is the other half: it strips what integrations still
attach, so a new integration cannot quietly start sending bodies again.
"""

from typing import Any

#: Headers dropped from every event. Lower-cased; matched case-insensitively.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-forwarded-for",
        "x-real-ip",
        "sec-websocket-protocol",
    }
)

REDACTED = "[redacted]"


def scrub_event(event: dict[str, Any], hint: Any = None) -> dict[str, Any]:
    """Sentry `before_send`: drop bodies, cookies, query strings, auth and IPs."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string", "env"):
            if key in request:
                request[key] = REDACTED
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                name: (REDACTED if name.lower() in SENSITIVE_HEADERS else value)
                for name, value in headers.items()
            }
    user = event.get("user")
    if isinstance(user, dict):
        # Keep the id so errors can still be grouped per account; drop the rest.
        event["user"] = {"id": user["id"]} if "id" in user else {}
    return _deep_scrub(event)


def _deep_scrub(event: dict[str, Any]) -> dict[str, Any]:
    """Messages, exception values, breadcrumbs and extras, while the
    ``telemetry_redaction`` switch is on (stream ops, handoff 35). Never
    raises: an error report must still go out if the scrub cannot run."""
    try:
        from api.services import features
        from api.services.ops.redaction import scrub_sentry_event

        if features.is_on("telemetry_redaction"):
            return scrub_sentry_event(event)
    except Exception:  # noqa: BLE001
        pass
    return event
