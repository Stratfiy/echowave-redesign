"""No literal route is unreachable behind an earlier parameterised one.

FastAPI matches routes in declaration order, not by specificity. A route like
POST /{workflow_id}/duplicate declared before POST /templates/duplicate wins
every request for the second one, and the caller gets a 422 complaining that
"templates" is not an integer — an error that says nothing about the real
cause. That shipped once and made "create an agent from a template"
unreachable from the UI.

Nothing warns about this: both routes appear in the OpenAPI schema and in
/docs, and only a real request reveals which one answers. So this walks the
whole app rather than pinning the one pair that broke.
"""

import pytest

from api.app import app


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _shadows(earlier: str, later: str) -> bool:
    """Would a request for `later` be answered by `earlier`?

    True when the two have the same shape and every segment of `earlier`
    either matches `later`'s literally or is a placeholder that swallows it.
    """
    a, b = _segments(earlier), _segments(later)
    if len(a) != len(b):
        return False
    return all(x == y or x.startswith("{") for x, y in zip(a, b))


def _routes():
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            if method in {"HEAD", "OPTIONS"}:
                continue
            yield method, route.path


def test_no_route_is_shadowed_by_an_earlier_one():
    ordered = list(_routes())
    shadowed = [
        (method, path, earlier)
        for i, (method, path) in enumerate(ordered)
        for earlier_method, earlier in ordered[:i]
        if earlier_method == method
        and "{" not in path
        and "{" in earlier
        and _shadows(earlier, path)
    ]
    assert not shadowed, (
        "unreachable routes — move each above the route that shadows it:\n"
        + "\n".join(f"  {m} {p} is shadowed by {e}" for m, p, e in shadowed)
    )


@pytest.mark.parametrize(
    ("earlier", "later", "expected"),
    [
        ("/{id}/duplicate", "/templates/duplicate", True),
        # Same length, but the literal segment differs — no shadowing.
        ("/{id}/validate", "/create/definition", False),
        # A placeholder only swallows one segment.
        ("/{id}", "/a/b", False),
    ],
)
def test_the_shadowing_rule_itself(earlier, later, expected):
    assert _shadows(earlier, later) is expected
