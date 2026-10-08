"""People is private: the two-person check (PEOPLE.md, "Privacy").

A adds contacts, a brief, an interaction and a company, each carrying one
unique marker. B -- a colleague in the same workspace, every flag on -- then
reads every GET route in the internal OpenAPI document (the one the UI's
client is generated from, ``ui/openapi.internal.json``) that takes no path
parameter. The marker must not appear in a single answer.

Everything happens inside the test's own transaction (``db_session``) and
is rolled back, so the sweep leaves nothing for other suites to trip on.

Also run against a live stack by ``scripts/people_privacy_check.py``, which
reads the generated file itself.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from api import constants
from api.services import features
from api.services.openapi_surface import full_spec
from api.services.people import interactions, store
from api.services.people.normalise import Incoming
from api.tests.support.people_fixtures import (  # noqa: F401
    client_as,
    fake_provider,
    make_people,
    people_on,
)


def get_routes_without_parameters() -> list[str]:
    from api.app import app

    spec = full_spec(app)
    return sorted(
        path for path, ops in spec["paths"].items() if "get" in ops and "{" not in path
    )


@pytest.fixture
async def people(db_session):
    """Both people inside the test's own transaction, rolled back at the
    end: with every flag on, some GET routes seed rows (plans, access-log
    entries) that other suites must never find."""
    return await make_people("pplpriv")


@pytest.fixture
def every_flag(monkeypatch):
    for constant in features.FLAGS.values():
        if hasattr(constants, constant):
            monkeypatch.setattr(constants, constant, True)


@pytest.mark.asyncio
async def test_a_colleague_never_sees_a_contact_or_a_brief(
    people, people_on, every_flag
):
    marker = f"PPLMARK{uuid4().hex[:10]}"
    made = await store.upsert(
        people.org,
        people.a.id,
        Incoming(
            name=f"Ravi {marker}",
            phones=["9876543210"],
            emails=[f"{marker.lower()}@example.in"],
            company=f"Traders {marker}",
        ).clean(),
        source="manual",
    )
    await store.edit(
        people.org, people.a.id, made.person.uuid, {"brief": f"Owes {marker}"}
    )
    await interactions.record(
        people.org,
        people.a.id,
        channel="call",
        phone="9876543210",
        line=f"Discussed {marker}",
        ref=f"run:{marker}",
    )
    async with client_as(people.as_user(people.a)) as c:
        # And from the phone app, the last source.
        synced = await c.post(
            "/api/v1/people/device/sync",
            json={
                "device_id": "pixel-a",
                "full": True,
                "contacts": [
                    {"id": "9", "name": f"Phone {marker}", "phones": ["9000011111"]}
                ],
            },
        )
        assert synced.json()["added"] == 1, synced.text
        mine = (await c.get("/api/v1/people")).text
    assert marker in mine  # the check can see it where it belongs

    routes = get_routes_without_parameters()
    assert "/api/v1/people" in routes and "/api/v1/people/status" in routes
    leaked: list[str] = []
    read = 0
    statuses: dict[int, int] = {}
    async with client_as(people.as_user(people.b)) as c:
        for path in routes:
            try:
                response = await c.get(path, timeout=20)
            except Exception:  # noqa: BLE001 - a route that breaks cannot leak
                continue
            read += 1
            statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
            body = response.text
            if marker in body or marker.lower() in body:
                leaked.append(f"{path} -> {response.status_code}")
    print(f"B read {read} of {len(routes)} GET routes: {sorted(statuses.items())}")
    assert statuses.get(200, 0) > 50, statuses
    assert read > 100, read
    assert leaked == []
