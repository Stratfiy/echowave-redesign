"""Error reports carry the stack trace, never the person."""

from api.utils.sentry_scrub import REDACTED, scrub_event


def test_bodies_cookies_queries_and_auth_headers_are_dropped():
    event = {
        "request": {
            "url": "https://app.example/api/v1/contacts",
            "data": {"phone": "+919800000000"},
            "cookies": {"session": "abc"},
            "query_string": "phone=%2B919800000000",
            "headers": {
                "Authorization": "Bearer secret",
                "X-Forwarded-For": "203.0.113.9",
                "Content-Type": "application/json",
            },
        },
        "user": {"id": 7, "email": "a@example.com", "ip_address": "203.0.113.9"},
    }

    scrubbed = scrub_event(event)

    request = scrubbed["request"]
    assert request["data"] == REDACTED
    assert request["cookies"] == REDACTED
    assert request["query_string"] == REDACTED
    assert request["headers"]["Authorization"] == REDACTED
    assert request["headers"]["X-Forwarded-For"] == REDACTED
    # What helps debugging stays.
    assert request["headers"]["Content-Type"] == "application/json"
    assert request["url"] == "https://app.example/api/v1/contacts"
    assert scrubbed["user"] == {"id": 7}


def test_an_event_without_a_request_passes_through():
    event = {"message": "worker crashed"}
    assert scrub_event(event) == {"message": "worker crashed"}


def test_the_api_initialises_sentry_without_default_pii():
    """Guard the init call itself, not only the hook."""
    import pathlib

    source = (
        pathlib.Path(__file__).parents[1] / "observability" / "sentry.py"
    ).read_text()
    assert "send_default_pii=False" in source
    assert "before_send=scrub_event" in source
