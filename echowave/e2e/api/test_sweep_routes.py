"""Sweep: every GET route with no path parameter answers, fast, without a 5xx.

One test per route per person, so a failure names the route. The list comes
from the API's own OpenAPI document (``routes.py``), so it grows on its own.

A 4xx is an answer: a member is refused an owner's screen, a route whose
required query cannot be guessed says 422. What fails is a 5xx, a timeout, or
an answer slower than ``E2E_ROUTE_BUDGET_SECONDS`` (5 by default).

One 5xx is not a failure: a 503 whose detail says the feature is not
configured on this deployment (Google sign-in with no OAuth client, TURN with
no secret). That is the honest state the product promises, and it is
reported as SKIP with the detail, so it is visible in every run and never
counted as a pass. Nor is a 404 from a route whose flag is off: that is
reported as SKIP naming the flag (``route_flags.json``).
"""

from __future__ import annotations

import os
import re

import pytest
from conftest import API, BASE
from routes import flags_for, get_routes, read

BUDGET_SECONDS = float(os.environ.get("E2E_ROUTE_BUDGET_SECONDS", "5"))

#: How a deliberate "not on this deployment" 503 reads. Matched against the
#: detail, never against the path, so a route cannot be waved through by name.
NOT_CONFIGURED = re.compile(
    r"not (configured|set up)|cannot reach|are not set", re.IGNORECASE
)


def pytest_generate_tests(metafunc):
    if "route" in metafunc.fixturenames:
        try:
            routes = get_routes(API)
        except Exception as exc:  # the API is down: one loud failure
            routes = []
            metafunc.parametrize("route", [pytest.param(None, id=f"openapi: {exc}")])
            return
        metafunc.parametrize("route", routes, ids=[r.id for r in routes])


def _check(client, route, require_flag):
    if route is None:
        pytest.fail(f"could not read {API}/openapi.json")
    answer = read(client.http, BASE, route, timeout=max(BUDGET_SECONDS * 3, 15))
    if answer.status == 0:
        pytest.fail(f"no answer in {answer.seconds:.1f}s")
    if answer.status == 429:
        pytest.fail("still rate-limited after waiting: this proves nothing")
    if answer.status == 404 and flags_for(route.path):
        # Dark by design: say which switch, never count it as a pass.
        require_flag(*flags_for(route.path), where=route.id)
    if answer.status == 503 and NOT_CONFIGURED.search(answer.body):
        pytest.skip(f"{route.id} not configured here: {answer.body[:160]}")
    assert answer.status < 500, f"HTTP {answer.status}: {answer.body[:300]}"
    assert answer.seconds <= BUDGET_SECONDS, (
        f"answered in {answer.seconds:.1f}s, budget {BUDGET_SECONDS:.0f}s"
    )


def test_route_answers_for_owner(a, route, require_flag):
    _check(a, route, require_flag)


def test_route_answers_for_member(b, route, require_flag):
    _check(b, route, require_flag)
