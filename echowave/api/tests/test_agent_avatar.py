"""An agent's face: chosen once, seen on every card and on the org chart.

The face is drawn by the browser; the server keeps the three choices (shape,
colour, resting expression) so every teammate sees the same agent. What must
hold: the choice round-trips through the database, it comes back on the agent
list and the team roster (one request for every card), an id the browser
cannot draw is refused, and another organisation's agent is out of reach.
"""

from __future__ import annotations

import pytest

from api.db.models import OrganizationModel, UserModel, WorkflowModel
from api.schemas.agent_avatar import read_avatar


async def _account(session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    user.selected_organization_id = org.id
    workflow = WorkflowModel(
        name=f"agent-{slug}", organization_id=org.id, user_id=user.id, status="active"
    )
    session.add(workflow)
    await session.flush()
    return org, user, workflow


FACE = {"shape": "nuage", "color": "violet", "expression": "curieux"}


@pytest.mark.asyncio
async def test_a_chosen_face_comes_back_on_the_list_and_the_roster(
    async_session, db_session, test_client_factory
):
    _, user, workflow = await _account(async_session, "face")

    async with test_client_factory(user) as client:
        put = await client.put(
            f"/api/v1/workflow/{workflow.id}/avatar", json={"avatar": FACE}
        )
        listing = await client.get("/api/v1/workflow/fetch")
        roster = await client.get("/api/v1/team/status")

    assert put.status_code == 200, put.text
    assert put.json()["avatar"] == FACE
    assert [w["avatar"] for w in listing.json() if w["id"] == workflow.id] == [FACE]
    assert [
        m["avatar"] for m in roster.json()["members"] if m["workflow_id"] == workflow.id
    ] == [FACE]


@pytest.mark.asyncio
async def test_null_puts_the_default_face_back(
    async_session, db_session, test_client_factory
):
    _, user, workflow = await _account(async_session, "reset")

    async with test_client_factory(user) as client:
        await client.put(
            f"/api/v1/workflow/{workflow.id}/avatar", json={"avatar": FACE}
        )
        cleared = await client.put(
            f"/api/v1/workflow/{workflow.id}/avatar", json={"avatar": None}
        )

    assert cleared.status_code == 200
    assert cleared.json()["avatar"] is None


@pytest.mark.asyncio
async def test_a_partial_face_fills_in_the_defaults(
    async_session, db_session, test_client_factory
):
    _, user, workflow = await _account(async_session, "partial")

    async with test_client_factory(user) as client:
        put = await client.put(
            f"/api/v1/workflow/{workflow.id}/avatar", json={"avatar": {"color": "vert"}}
        )

    assert put.json()["avatar"] == {
        "shape": "cercle",
        "color": "vert",
        "expression": "neutre",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "avatar",
    [
        {"shape": "star"},
        {"color": "#ff0000"},
        {"expression": "angry"},
        {"shape": "cercle", "hat": "yes"},
    ],
)
async def test_a_face_the_browser_cannot_draw_is_refused(
    async_session, db_session, test_client_factory, avatar
):
    _, user, workflow = await _account(
        async_session, f"bad-{len(avatar)}-{next(iter(avatar))}"
    )

    async with test_client_factory(user) as client:
        put = await client.put(
            f"/api/v1/workflow/{workflow.id}/avatar", json={"avatar": avatar}
        )

    assert put.status_code == 422


@pytest.mark.asyncio
async def test_another_organisations_agent_is_out_of_reach(
    async_session, db_session, test_client_factory
):
    _, mine, _ = await _account(async_session, "mine")
    _, _, theirs = await _account(async_session, "theirs")

    async with test_client_factory(mine) as client:
        put = await client.put(
            f"/api/v1/workflow/{theirs.id}/avatar", json={"avatar": FACE}
        )

    assert put.status_code == 404
    await async_session.refresh(theirs)
    assert theirs.avatar is None


def test_a_stored_face_that_no_longer_validates_reads_as_the_default():
    assert read_avatar(None) is None
    assert read_avatar("nuage") is None
    assert read_avatar({"shape": "retired-shape"}) is None
    assert read_avatar(FACE).model_dump() == FACE
