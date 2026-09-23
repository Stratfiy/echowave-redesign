"""Tata Smartflo: the Call Detail Records API.

``GET https://api-smartflo.tatateleservices.com/v1/call/records`` with
``Authorization: Bearer <API token>``, ``from_date`` and ``to_date`` as
``Y-m-d H:i:s`` in IST, paged by ``page`` and ``limit`` against ``count``.
The token is one generated in the Smartflo portal; tokens made after
9 Aug 2026 expire in at most 90 days, so a refused token is reported as
needing a new one rather than retried.

Smartflo names the agent (``agent_name``, ``agent_number``) and the customer
(``client_number``). The recording URL carries its own token in the query
string; the bearer header is sent as well, which it ignores.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from api.services.dialer_import._base import (
    DialerCall,
    as_int,
    client_for,
    download,
    raise_for,
)
from api.services.dialer_import.times import format_ist, parse_ist

VENDOR = "smartflo"
FIELDS = ("api_token",)
BASE_URL = "https://api-smartflo.tatateleservices.com"
PAGE_SIZE = 100
MAX_PAGES = 50


def _headers(credentials: dict) -> dict[str, str]:
    token = credentials["api_token"].strip().removeprefix("Bearer ").strip()
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _parse(record: dict) -> DialerCall:
    started = None
    if record.get("date"):
        started = parse_ist(f"{record['date']} {record.get('time') or '00:00:00'}")
    answered = as_int(record.get("answered_seconds"))
    return DialerCall(
        external_id=str(record.get("call_id") or record.get("id") or ""),
        started_at=started,
        duration_seconds=answered or as_int(record.get("call_duration")),
        direction=(record.get("direction") or None),
        status="answered" if answered > 0 else str(record.get("status") or ""),
        recording_url=record.get("recording_url") or None,
        agent_name=record.get("agent_name") or None,
        agent_number=record.get("agent_number") or None,
        customer_number=record.get("client_number") or None,
    )


async def list_calls(
    credentials: dict,
    since: datetime,
    until: datetime,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[DialerCall]:
    calls: list[DialerCall] = []
    async with client_for(client) as client:
        for page in range(1, MAX_PAGES + 1):
            response = await client.get(
                f"{BASE_URL}/v1/call/records",
                headers=_headers(credentials),
                params={
                    "from_date": format_ist(since),
                    "to_date": format_ist(until),
                    "page": page,
                    "limit": PAGE_SIZE,
                },
            )
            _raise_for(response)
            body = response.json()
            results = body.get("results") or []
            calls += [_parse(r) for r in results if r.get("call_id") or r.get("id")]
            if not results or page * PAGE_SIZE >= as_int(body.get("count")):
                break
    return calls


async def fetch_recording(
    credentials: dict, call: DialerCall, *, client: httpx.AsyncClient | None = None
) -> bytes:
    return await download(
        call, client=client, check=_raise_for, headers=_headers(credentials)
    )


def _raise_for(response: httpx.Response) -> None:
    raise_for(
        response,
        dialer="Smartflo",
        refused=(
            "the API token -- it may have expired. Generate a "
            "new token in the Smartflo portal and connect again."
        ),
    )


__all__ = ["FIELDS", "VENDOR", "fetch_recording", "list_calls"]
