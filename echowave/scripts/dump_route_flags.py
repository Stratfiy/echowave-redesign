"""Write which flag guards which route, for the end-to-end suite.

    python -m scripts.dump_route_flags

A route behind a switched-off flag answers a plain 404 (``features.require``),
deliberately indistinguishable from a route that does not exist. That is
right for a customer and useless to a test: the end-to-end sweep in
``e2e/api`` would count every dark route as a pass. So the sweep reads this
map and reports such a route as ``SKIP ... flag <name> is off`` instead.

The map is built from the app itself (the ``feature`` attribute every
``features.require`` dependency carries), never from reading source, and
``api/tests/test_e2e_route_flags_current.py`` fails when the checked-in copy
is stale -- so a new flagged router is mapped by the same commit that adds it.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "e2e" / "api" / "route_flags.json"


def _flags(dependant) -> set[str]:
    found: set[str] = set()
    for dependency in dependant.dependencies:
        name = getattr(dependency.call, "feature", None)
        if isinstance(name, str):
            found.add(name)
        found |= _flags(dependency)
    return found


def route_flags(app) -> dict[str, dict[str, list[str]]]:
    """``{path: {METHOD: [flag, ...]}}`` for every route a flag guards."""
    from fastapi.routing import APIRoute

    mapping: dict[str, dict[str, list[str]]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        flags = sorted(_flags(route.dependant))
        if not flags:
            continue
        for method in sorted(route.methods or ()):
            mapping.setdefault(route.path, {})[method] = flags
    return dict(sorted(mapping.items()))


def render(app) -> str:
    return json.dumps(route_flags(app), indent=1, sort_keys=True) + "\n"


def main() -> None:
    from loguru import logger

    logger.remove()
    from api.app import app

    OUTPUT.write_text(render(app))
    print(f"Wrote {OUTPUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
