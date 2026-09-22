"""Exotel: the bulk Call Details API, and the recording behind each call.

``GET https://<subdomain>/v1/Accounts/<sid>/Calls.json`` with the API key and
token as basic auth, filtered by ``DateCreated=gte:...;lte:...`` in IST, up
to 100 a page, following ``Metadata.NextPageUri``. Each call carries a
``RecordingUrl`` that is valid for 24 hours and needs the same credentials,
which is why recordings are copied across on the night rather than linked.

Which side is the telecaller depends on direction. An ``outbound-api`` call
is Exotel's click-to-call: the first leg rings the agent (``From``) and then
connects the customer (``To``). An inbound call arrives from the customer
and is routed to an agent, who is the second leg's number when Exotel
returns ``Details``. Exotel does not name agents, so the name is left to the
business to map from the number.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

import httpx

from api.services.dialer_import._base import (
    MAX_RECORDING_BYTES,
    TIMEOUT,
    DialerAuthError,
    DialerCall,
    DialerError,
    as_int,
)
from api.services.dialer_import.times import format_ist, parse_ist

VENDOR = "exotel"
#: What a business pastes from Exotel's API settings page.
FIELDS = ("api_key", "api_token", "account_sid", "subdomain")
DEFAULT_SUBDOMAIN = "api.exotel.com"
PAGE_SIZE = 100
#: A day of a busy team is a few hundred calls; this bounds a runaway listing.
MAX_PAGES = 50


def secret_of(credentials: dict) -> str:
    return credentials["api_token"]


def _base(credentials: dict) -> str:
    host = (credentials.get("subdomain") or DEFAULT_SUBDOMAIN).strip()
    host = host.removeprefix("https://").removeprefix("http://").strip("/")
    return f"https://{host}"


def _auth(credentials: dict) -> tuple[str, str]:
    return (credentials["api_key"], credentials["api_token"])


def _parse(call: dict) -> DialerCall:
    direction = (call.get("Direction") or "").lower()
    details = call.get("Details") or {}
    if direction.startswith("outbound"):
        agent, customer = call.get("From"), call.get("To")
    else:
        agent = details.get("Leg2To") or None
        customer = call.get("From")
    return DialerCall(
        external_id=str(call.get("Sid") or ""),
        started_at=parse_ist(call.get("StartTime") or call.get("DateCreated")),
        duration_seconds=as_int(
            details.get("ConversationDuration") or call.get("Duration")
        ),
        direction="outbound" if direction.startswith("outbound") else "inbound",
        status=str(call.get("Status") or ""),
        recording_url=call.get("RecordingUrl") or None,
        agent_name=None,
        agent_number=agent,
        customer_number=customer,
    )


async def list_calls(
    credentials: dict,
    since: datetime,
    until: datetime,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[DialerCall]:
    base = _base(credentials)
    url: str | None = f"{base}/v1/Accounts/{credentials['account_sid']}/Calls.json"
    params: dict | None = {
        "DateCreated": f"gte:{format_ist(since)};lte:{format_ist(until)}",
        "PageSize": PAGE_SIZE,
        "details": "true",
    }
    calls: list[DialerCall] = []
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        for _ in range(MAX_PAGES):
            if not url:
                break
            response = await client.get(url, params=params, auth=_auth(credentials))
            _raise_for(response)
            body = response.json()
            calls += [_parse(c) for c in body.get("Calls") or [] if c.get("Sid")]
            next_uri = (body.get("Metadata") or {}).get("NextPageUri")
            url = urljoin(base, next_uri) if next_uri else None
            params = None  # the next-page URI carries the filters
    finally:
        if own:
            await client.aclose()
    return calls


async def fetch_recording(
    credentials: dict, call: DialerCall, *, client: httpx.AsyncClient | None = None
) -> bytes:
    if not call.recording_url:
        raise DialerError("This call has no recording.")
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True)
    try:
        response = await client.get(call.recording_url, auth=_auth(credentials))
    finally:
        if own:
            await client.aclose()
    _raise_for(response)
    if len(response.content) > MAX_RECORDING_BYTES:
        raise DialerError("The recording is too long to import.")
    return response.content


def _raise_for(response: httpx.Response) -> None:
    if response.status_code in (401, 403):
        raise DialerAuthError(
            "Exotel refused the API key and token. Check them on Exotel's API "
            "settings page and connect again."
        )
    if response.status_code >= 400:
        raise DialerError(
            f"Exotel answered {response.status_code}. It will be tried again tonight."
        )


__all__ = ["FIELDS", "VENDOR", "fetch_recording", "list_calls", "secret_of"]
