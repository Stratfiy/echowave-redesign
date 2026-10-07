"""Stream `reach`: one person's connections never show to a colleague.

Decibyl's thread may be shared (private threads are a separate switch). So
every row stream `reach` writes -- the connect chip, the comparison, the
order or outside-tool card, and the lines under it -- names its person
(``private_to``), and the timeline returns it only to them. Deny by
default: a reader that names nobody gets none of them.
"""

from __future__ import annotations

import pytest

from api.db import db_client
from api.services.workflow import actions, decibyl
from api.tests.support.reach_fixtures import (  # noqa: F401
    acting_as,
    call,
    client_as,
    connect_zomato,
    fake_servers,
    no_queue,
    people,
    reach_on,
)

pytestmark = pytest.mark.asyncio

REACH_KINDS = {"reach_connect_offered", "reach_comparison"}


async def _thread(user):
    async with client_as(user) as client:
        response = await client.get(
            "/api/v1/timeline", params={"assistant": "true", "limit": 100}
        )
    assert response.status_code == 200
    return response.json()["events"]


async def _a_does_everything(people):
    await connect_zomato(people.org, people.a.id)
    with acting_as(people.a.id):
        await decibyl._tool(
            people.org, call("connect_outside_tool", name="Linear"), people.a.id
        )
        await decibyl._tool(
            people.org, call("compare_prices", items=["paneer"]), people.a.id
        )
        card = await decibyl._tool(
            people.org,
            call(
                "order_prepare",
                app="zomato",
                store_id="r1",
                items=[{"item_id": "i1", "quantity": 1}],
                address_id="a1",
            ),
            people.a.id,
        )
    event = await db_client.get_agent_event(
        card["event_id"], organization_id=people.org
    )
    await actions.settle(
        organization_id=people.org,
        event_id=event.id,
        verb="confirm",
        user_id=people.a.id,
        version=event.payload.get("version"),
    )
    await actions.run(event.id, people.org)
    return event


class TestTheThread:
    async def test_the_person_sees_their_chip_comparison_card_and_outcome(
        self, reach_on, people, no_queue
    ):
        card = await _a_does_everything(people)
        events = await _thread(people.a)
        kinds = {e["kind"] for e in events}
        assert REACH_KINDS <= kinds
        assert card.id in {e["id"] for e in events}
        assert any(
            (e.get("payload") or {}).get("action_event_id") == card.id for e in events
        ), "the line saying the order was placed"

    async def test_a_colleague_on_the_same_thread_sees_none_of_it(
        self, reach_on, people, no_queue
    ):
        card = await _a_does_everything(people)
        events = await _thread(people.b)
        assert not REACH_KINDS & {e["kind"] for e in events}
        assert card.id not in {e["id"] for e in events}
        assert not any(
            (e.get("payload") or {}).get("action_event_id") == card.id for e in events
        )
        assert "Zomato" not in str(events)

    async def test_nor_does_decibyl_in_the_colleagues_turn(
        self, reach_on, people, no_queue
    ):
        await _a_does_everything(people)
        with acting_as(people.b.id):
            history = await decibyl._history(people.org)
        assert "Zomato" not in str(history) and "Spice Route" not in str(history)
        with acting_as(people.a.id):
            assert "Spice Route" in str(await decibyl._history(people.org))

    async def test_a_reader_that_names_nobody_gets_none(
        self, reach_on, people, no_queue
    ):
        await _a_does_everything(people)
        rows = await db_client.agent_events(
            organization_id=people.org, assistant_thread=True, limit=100
        )
        assert not [r for r in rows if (r.payload or {}).get("private_to")]
