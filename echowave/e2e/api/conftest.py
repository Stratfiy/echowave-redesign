"""Shared fixtures for the end-to-end API suite.

Everything here talks to a running Decibyl over real HTTP, exactly as the app
does: no imports from ``api/``, no database, no mocks. Point it at a stack
with ``STAGING_URL`` (default: the API on loopback, which is where staging
lives on the CI box) and two accounts in one workspace:

    STAGING_EMAIL_A / STAGING_PASSWORD_A   the workspace owner
    STAGING_EMAIL_B / STAGING_PASSWORD_B   a plain member of the same workspace

Passwords are never printed: they go into a request body and nowhere else.

Skips are loud on purpose. A route behind a switched-off flag is reported as
``SKIP ... flag <name> is off``, never as a pass, so a run with half the
product dark cannot read as a green run of the whole product.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pace
import pytest

BASE = os.environ.get("STAGING_URL", "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api/v1"
TIMEOUT = float(os.environ.get("E2E_HTTP_TIMEOUT", "30"))


@dataclass
class Client:
    """One signed-in person. ``get``/``post``/... return the httpx response."""

    label: str
    email: str
    token: str
    me: dict[str, Any] = field(default_factory=dict)
    http: httpx.Client | None = None

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        assert self.http is not None
        for _ in range(pace.RETRIES_ON_429):
            pace.wait_turn()
            response = self.http.request(method, f"{API}{path}", **kwargs)
            if response.status_code != 429:
                return response
            time.sleep(pace.retry_after(response.headers))
        return response

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, json: Any = None, **kwargs) -> httpx.Response:
        return self.request("POST", path, json=json, **kwargs)

    def put(self, path: str, json: Any = None, **kwargs) -> httpx.Response:
        return self.request("PUT", path, json=json, **kwargs)

    def patch(self, path: str, json: Any = None, **kwargs) -> httpx.Response:
        return self.request("PATCH", path, json=json, **kwargs)

    def delete(self, path: str, **kwargs) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)

    @property
    def user_id(self) -> int | None:
        return self.me.get("id")

    @property
    def organization_id(self) -> int | None:
        return self.me.get("organization_id")


def _sign_in(label: str, email_var: str, password_var: str) -> Client:
    email = os.environ.get(email_var)
    password = os.environ.get(password_var)
    if not email or not password:
        pytest.fail(f"{email_var}/{password_var} are not set", pytrace=False)
    http = httpx.Client(timeout=TIMEOUT, follow_redirects=False)
    for _ in range(pace.RETRIES_ON_429):
        pace.wait_turn()
        response = http.post(
            f"{API}/auth/login", json={"email": email, "password": password}
        )
        if response.status_code != 429:
            break
        time.sleep(pace.retry_after(response.headers))
    body = response.json() if response.content else {}
    token = body.get("token") or body.get("access_token")
    if response.status_code != 200 or not token:
        http.close()
        pytest.fail(
            f"sign in as {label} ({email}) failed: HTTP {response.status_code}",
            pytrace=False,
        )
    http.headers["authorization"] = f"Bearer {token}"
    client = Client(label=label, email=email, token=token, http=http)
    client.me = expect(client.get("/auth/me"))
    return client


@pytest.fixture(scope="session")
def health() -> dict[str, Any]:
    response = httpx.get(f"{API}/health", timeout=TIMEOUT)
    if response.status_code != 200:
        pytest.exit(f"{API}/health answered HTTP {response.status_code}", 2)
    return response.json()


@pytest.fixture(scope="session")
def features(health) -> dict[str, bool]:
    return health.get("features") or {}


@pytest.fixture(scope="session")
def a() -> Client:
    client = _sign_in("A", "STAGING_EMAIL_A", "STAGING_PASSWORD_A")
    yield client
    client.http.close()


@pytest.fixture(scope="session")
def b(a) -> Client:
    client = _sign_in("B", "STAGING_EMAIL_B", "STAGING_PASSWORD_B")
    if client.organization_id != a.organization_id:
        pytest.fail(
            "A and B are not in one workspace: invite B from A once, by hand",
            pytrace=False,
        )
    yield client
    client.http.close()


@pytest.fixture(scope="session")
def require_flag(a, features):
    """``require_flag("saved_items")`` skips, naming the flag, when it is off.

    Reads the flags as A sees them (``/features`` folds in per-workspace
    switches from the staff console), falling back to ``/health``.
    """
    response = a.get("/features")
    effective = dict(features)
    if response.status_code == 200 and isinstance(response.json(), dict):
        body = response.json()
        effective.update(body.get("features", body))

    def _require(*names: str, where: str = "") -> None:
        off = [name for name in names if not effective.get(name)]
        if off:
            pytest.skip(f"{where + ': ' if where else ''}flag {', '.join(off)} is off")

    return _require


@pytest.fixture(scope="session")
def run_id() -> str:
    """One id per run, so leftovers from a crashed run are recognisable."""
    return uuid.uuid4().hex[:10]


@pytest.fixture
def marker(run_id) -> str:
    """A string that appears nowhere but in what this test creates."""
    return f"e2e-{run_id}-{uuid.uuid4().hex[:8]}"


def wait_for(fetch, *, timeout: float, interval: float = 2.0):
    """Call ``fetch()`` until it returns something truthy or time runs out."""
    deadline = time.monotonic() + timeout
    while True:
        value = fetch()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def expect(response: httpx.Response, *statuses: int) -> Any:
    """Assert the status, with the body in the message when it is wrong."""
    wanted = statuses or (200,)
    assert response.status_code in wanted, (
        f"{response.request.method} {response.request.url.path} -> "
        f"HTTP {response.status_code}: {response.text[:500]}"
    )
    return response.json() if response.content else None
