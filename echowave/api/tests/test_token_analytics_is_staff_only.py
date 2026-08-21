"""Token analytics are staff-only, and stay that way.

A per-account token series looks harmless once per-model *cost* is stripped
out of it — which an earlier pass did. It is not. The same account can read its
own spend from `/api/v1/organizations/usage/spend`, and spend divided by tokens
over the same period is a blended per-token price. On an account running one
language model, "blended" is our price for that model: one step from the
vendor's published rate card, and therefore one step from our markup.

The accounts most motivated to do that division are the ones being paid a
percentage of it — resellers on a commission against the platform fee.

So this is a pricing boundary rather than a tenant boundary, and it is the kind
that gets reopened by accident: the aggregation is already written, already
takes an `organization_id`, and reads as a nice thing to give a customer. This
test is here to make putting it back a decision rather than a convenience.

Staff lose nothing — `/api/v1/admin/billing/tokens` carries the full per-model
picture including cost, takes an `organization_id`, and is behind
`get_superuser`.
"""

from __future__ import annotations

import pytest

from api.app import app

#: Prefixes a customer holds a bearer token for. Everything under `/admin/` is
#: behind `get_superuser`; everything here is reachable with an ordinary login.
CUSTOMER_PREFIX = "/api/v1/organizations/"
STAFF_PREFIX = "/api/v1/admin/"


def _paths() -> list[str]:
    """Every path the app actually serves, mounts and sub-routers included.

    This used to read ``route.path`` straight off ``app.routes``. It no longer
    can: FastAPI 0.141 defers router inclusion, so ``app.routes`` holds
    ``_IncludedRouter`` wrappers that carry no ``path`` at all. The old
    comprehension used ``getattr(route, "path", "")`` and silently dropped every
    one of them — leaving five built-in paths, of which none is under
    ``/api/v1/organizations/``.

    Two tests in this file failed loudly when that happened. The one that
    matters passed **vacuously**: the boundary check searched five paths for an
    offender, found none, and reported the pricing boundary intact without ever
    looking at a real route. A guard that cannot fail is not a guard, which is
    why this walks the tree rather than trusting one level of it.
    """
    paths = [path for path, _ in _walk(app.routes)]
    # The walk finding nothing, or almost nothing, is the failure this docstring
    # describes recurring under a future version. Fail here rather than passing
    # every assertion below against an empty list.
    assert len(paths) > 100, (
        f"Route enumeration found only {len(paths)} paths, so every check in "
        "this file would pass without inspecting the real API surface. The "
        "walk above needs updating for this FastAPI version."
    )
    return paths


def _route_for(path: str):
    """The route object serving ``path``, for tests that inspect its parameters.

    Same reason as :func:`_paths`: a plain scan of ``app.routes`` cannot see
    past a deferred inclusion, so looking a route up there raises
    ``StopIteration`` for every route the API actually serves.
    """
    for found, route in _walk(app.routes):
        if found == path:
            return route
    raise AssertionError(f"No route serves {path}. Known paths: {len(_paths())}")


def _walk(routes, prefix: str = ""):
    """Every (path, route) the app serves, descending through inclusions."""
    found: list[tuple[str, object]] = []
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            # A deferred inclusion. Its prefix lives on the include context,
            # not on the wrapper.
            context = getattr(route, "include_context", None)
            found += _walk(included.routes, prefix + getattr(context, "prefix", ""))
        elif hasattr(route, "routes") and not hasattr(route, "endpoint"):
            # A Mount — the MCP app is one. Reachable, so it counts.
            found += _walk(route.routes, prefix + getattr(route, "path", ""))
        elif hasattr(route, "path"):
            found.append((prefix + route.path, route))
    return found


def test_no_customer_facing_route_serves_token_analytics():
    offenders = [
        path
        for path in _paths()
        if path.startswith(CUSTOMER_PREFIX) and "token" in path.lower()
    ]
    assert offenders == [], (
        "These customer-scoped routes expose token analytics. Tokens divided "
        "into the spend on /usage/spend give our blended per-token price: "
        f"{offenders}"
    )


def test_the_removed_route_is_actually_gone():
    """Named explicitly, because this is the one that existed and was removed.

    A path-shape assertion alone would pass if somebody reintroduced it under a
    slightly different name.
    """
    assert "/api/v1/organizations/usage/tokens" not in _paths()


def test_staff_keep_a_token_view():
    """The other half of the trade. Removing the customer route is only
    acceptable because staff can still answer "what is this account burning" —
    if that ever goes too, the removal stops being a boundary and starts being
    a blind spot."""
    assert "/api/v1/admin/billing/tokens" in _paths()


def test_the_staff_token_route_can_narrow_to_one_account():
    """Staff support a specific customer, not the whole platform at once."""
    route = _route_for("/api/v1/admin/billing/tokens")
    names = {p.name for p in route.dependant.query_params}
    for dep in route.dependant.dependencies:
        names |= {p.name for p in dep.query_params}
    assert "organization_id" in names, sorted(names)


@pytest.mark.parametrize("survivor", ["/usage/spend", "/usage/calls"])
def test_the_customers_own_bill_survives(survivor: str):
    """Guard against over-correction. Spend is what the customer paid, and a
    bill discloses the price, never the cost — removing it would leave them
    unable to answer "why is my bill that number", which is the question the
    analytics screens exist for."""
    assert f"{CUSTOMER_PREFIX.rstrip('/')}{survivor}" in _paths()
