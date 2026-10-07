"""The end-to-end suite's map of flagged routes matches the app.

``e2e/api/route_flags.json`` tells the staging sweeps which flag guards which
route, so a dark route is reported as ``SKIP ... flag <name> is off`` rather
than counted as a pass. A stale map would quietly turn new dark routes back
into passes, so this fails until the map is regenerated:

    python -m scripts.dump_route_flags
"""

from __future__ import annotations

from api.app import app
from scripts import dump_route_flags


def test_the_checked_in_map_is_current():
    assert dump_route_flags.OUTPUT.read_text() == dump_route_flags.render(app), (
        "e2e/api/route_flags.json is stale: run python -m scripts.dump_route_flags"
    )


def test_a_known_flagged_route_is_mapped():
    # Arrival: the walk must find flags on real routes, not return an empty
    # map that would trivially equal an empty file.
    mapping = dump_route_flags.route_flags(app)
    assert mapping["/api/v1/meetings"]["GET"] == ["meeting_capture"]
    assert "saved_items" in mapping["/api/v1/me/saved"]["GET"]
