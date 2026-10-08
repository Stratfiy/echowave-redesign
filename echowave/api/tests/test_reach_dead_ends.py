"""Stream `reach`: every dead end offers the way past it, in the thread.

Found running the stream end to end (phase 3): an ordering app whose sign-in
had lapsed answered "connect it again" with no chip to do it with; a
comparison with nothing connected put nothing on the thread; an order edited
on its card, and the line saying what happened to a card, landed on the
person's original chat instead of the one they were in.
"""

from __future__ import annotations

import pytest

from api.db import db_client
from api.services.workflow import actions, agent_timeline, decibyl
from api.tests.support import reach_fakes
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

THREAD = "thread-reach-dead-ends"
LIST = [{"item_id": "i1", "quantity": 2}]


async def _ask(people, tool, **arguments):
    with acting_as(people.a.id), agent_timeline.in_thread(THREAD):
        return await decibyl._tool(
            people.org, call(tool, **arguments), people.a.id, thread_id=THREAD
        )


async def _chips(people):
    rows = await db_client.agent_events(
        organization_id=people.org,
        kinds=["reach_connect_offered"],
        limit=20,
        assistant_thread=True,
        thread_id=THREAD,
        viewer_id=people.a.id,
    )
    return [r.payload for r in rows]


def _sign_in_lapses():
    """The app forgets the token it issued: its next answer is a 401."""
    reach_fakes._TOKENS.clear()


class TestALapsedSignIn:
    @pytest.mark.parametrize(
        "tool, arguments",
        [
            ("order_search", {"app": "zomato", "query": "paneer"}),
            (
                "order_prepare",
                {"app": "zomato", "store_id": "r1", "items": LIST, "address_id": "a1"},
            ),
            ("compare_prices", {"items": ["paneer"]}),
        ],
    )
    async def test_puts_a_chip_on_the_thread(self, reach_on, people, tool, arguments):
        await connect_zomato(people.org, people.a.id)
        _sign_in_lapses()
        result = await _ask(people, tool, **arguments)
        chips = await _chips(people)
        assert [c["provider"] for c in chips] == ["zomato"], result
        assert chips[0]["state"] == "available"
        assert "expired" in chips[0]["why"]
        assert "NeedsSignIn" not in str(result)
        # The model is told the chip is there, so it can say so.
        assert "chip" in str(result)

    async def test_signing_in_again_on_the_chip_works(self, reach_on, people):
        await connect_zomato(people.org, people.a.id)
        _sign_in_lapses()
        await _ask(people, "order_search", app="zomato", query="paneer")
        await connect_zomato(people.org, people.a.id)
        result = await _ask(people, "order_search", app="zomato", query="paneer")
        assert result.get("untrusted") is True and "Paneer Tikka" in result["data"]


class TestNothingToCompare:
    async def test_offers_the_app_that_could_be_connected(self, reach_on, people):
        result = await _ask(people, "compare_prices", items=["paneer"])
        chips = await _chips(people)
        # Zomato can be connected: a chip for it. Swiggy cannot yet: no
        # chip pretending it can, and the sentence still says why.
        assert [c["provider"] for c in chips] == ["zomato"]
        assert "Swiggy" in str(result) and "chip" in str(result)

    async def test_the_reason_reads_as_sentences(self, reach_on, people):
        result = await _ask(people, "compare_prices", items=["paneer"])
        assert ".;" not in str(result) and "..)" not in str(result)


class TestTheThreadItHappenedOn:
    async def test_an_edited_order_stays_on_its_thread(self, reach_on, people):
        await connect_zomato(people.org, people.a.id)
        first = await _ask(
            people,
            "order_prepare",
            app="zomato",
            store_id="r1",
            items=LIST,
            address_id="a1",
        )
        old = await db_client.get_agent_event(
            first["event_id"], organization_id=people.org
        )
        assert old.thread_id == THREAD
        async with client_as(people.a) as client:
            response = await client.post(
                f"/api/v1/reach/orders/{old.payload['args']['draft']}/revise",
                json={"items": [{"item_id": "i1", "quantity": 1}]},
            )
        assert response.status_code == 200, response.text
        new = await db_client.get_agent_event(
            response.json()["event_id"], organization_id=people.org
        )
        assert new.thread_id == THREAD

    async def test_the_line_under_a_card_stays_on_its_thread(
        self, reach_on, people, no_queue
    ):
        await connect_zomato(people.org, people.a.id)
        proposed = await _ask(
            people,
            "order_prepare",
            app="zomato",
            store_id="r1",
            items=LIST,
            address_id="a1",
        )
        event = await db_client.get_agent_event(
            proposed["event_id"], organization_id=people.org
        )
        await actions.settle(
            organization_id=people.org,
            event_id=event.id,
            verb="confirm",
            user_id=people.a.id,
            version=(event.payload or {}).get("version"),
        )
        # The card's job runs in the worker, outside any turn's thread.
        await actions.run(event.id, people.org)
        rows = await db_client.agent_events(
            organization_id=people.org,
            kinds=["message"],
            limit=20,
            assistant_thread=True,
            thread_id=THREAD,
            viewer_id=people.a.id,
        )
        assert any(
            (r.payload or {}).get("action_event_id") == event.id for r in rows
        ), "the 'Ordered on Zomato' line is on the thread the card is on"
        original = await db_client.agent_events(
            organization_id=people.org,
            kinds=["message"],
            limit=20,
            assistant_thread=True,
            viewer_id=people.a.id,
        )
        assert not any(
            (r.payload or {}).get("action_event_id") == event.id for r in original
        ), "and not on the person's original chat"
