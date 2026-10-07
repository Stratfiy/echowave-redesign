"""A refused request is a 429 the browser can read, not a network error.

The rate limit wrapped the app outside CORS, so its 429 went out with no
``Access-Control-Allow-Origin``. Wherever the web app and the API are on
different origins -- staging on one box, 3010 and 8000 -- the browser then
hid the 429 and its ``Retry-After`` from the page, which saw only "Failed
to fetch" (the onboarding check logged exactly that). Found by the
end-to-end browser suite clicking through Settings at a person's pace.
"""

from __future__ import annotations

import httpx

import api.middleware_rate_limit as mw
from api.app import app


async def test_a_429_carries_the_cors_headers(monkeypatch):
    async def refuse(**_):
        return False, 17

    monkeypatch.setattr(mw.rate_limiter, "check", refuse)
    monkeypatch.setattr(mw, "RATE_LIMIT_ENABLED", True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.get(
            "/api/v1/timeline/recents",
            headers={"origin": "http://127.0.0.1:3010"},
        )

    assert response.status_code == 429
    assert response.headers.get("retry-after") == "17"
    assert response.headers.get("access-control-allow-origin") in (
        "*",
        "http://127.0.0.1:3010",
    )


async def test_the_limit_still_answers_before_the_route(monkeypatch):
    # Moving it inside CORS must not move it past auth: a refused request
    # never reaches the handler (no 401 for a missing token, a 429).
    async def refuse(**_):
        return False, 5

    monkeypatch.setattr(mw.rate_limiter, "check", refuse)
    monkeypatch.setattr(mw, "RATE_LIMIT_ENABLED", True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.get("/api/v1/timeline/recents")

    assert response.status_code == 429
