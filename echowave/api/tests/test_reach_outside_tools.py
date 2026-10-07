"""Stream `reach`: outside AI tools used from Chat, and the connect chip.

Done when: a person's connected tools appear in their own Chat turns (and
nobody else's); a read runs and comes back as labelled data; a write is a
card only they can confirm, which then runs once; and a tool that is not
connected is a chip on the thread, never a trip to another screen.
"""

from __future__ import annotations

import pytest

from api.db import db_client
from api.services.reach import outside_tools
from api.services.workflow import actions, decibyl
from api.tests.support import reach_fakes
from api.tests.support.reach_fixtures import (  # noqa: F401
    acting_as,
    call,
    connect_notes,
    fake_servers,
    no_queue,
    people,
    reach_on,
)

pytestmark = pytest.mark.asyncio


async def _names(org, user_id):
    with acting_as(user_id):
        return {t["name"] for t in await decibyl.tools_for(org, {})}


class TestInChat:
    async def test_the_persons_tools_appear_in_their_turn(self, reach_on, people):
        await connect_notes(people.org, people.a.id, reach_on.base)
        names = await _names(people.org, people.a.id)
        assert {"ext_notes__search_notes", "ext_notes__create_note"} <= names

    async def test_and_not_in_a_colleagues(self, reach_on, people):
        await connect_notes(people.org, people.a.id, reach_on.base)
        assert not {
            n for n in await _names(people.org, people.b.id) if n.startswith("ext_")
        }

    async def test_a_colleague_calling_the_name_reaches_nothing(self, reach_on, people):
        await connect_notes(people.org, people.a.id, reach_on.base)
        with acting_as(people.b.id):
            result = await decibyl._tool(
                people.org, call("ext_notes__search_notes", query="x"), people.b.id
            )
        assert result["status"] == "unavailable"

    async def test_a_read_runs_and_comes_back_as_data(self, reach_on, people):
        await connect_notes(people.org, people.a.id, reach_on.base)
        with acting_as(people.a.id):
            result = await decibyl._tool(
                people.org,
                call("ext_notes__search_notes", query="groceries"),
                people.a.id,
            )
        assert result["status"] == "success" and result["untrusted"] is True
        assert "milk, eggs" in result["data"]
        assert decibyl._was_a_read(call("ext_notes__search_notes"), result)

    async def test_a_write_is_a_card_that_runs_once(self, reach_on, people, no_queue):
        await connect_notes(people.org, people.a.id, reach_on.base)
        with acting_as(people.a.id):
            result = await decibyl._tool(
                people.org, call("ext_notes__create_note", text="buy rice"), people.a.id
            )
        assert result["status"] == "proposed"
        assert reach_fakes.NOTES == []
        event = await db_client.get_agent_event(
            result["event_id"], organization_id=people.org
        )
        assert event.payload["action"] == "run_outside_tool"
        assert event.payload["label"] == "create_note on Notes"
        with pytest.raises(actions.ActionError):
            await actions.settle(
                organization_id=people.org,
                event_id=event.id,
                verb="confirm",
                user_id=people.b.id,
            )
        await actions.settle(
            organization_id=people.org,
            event_id=event.id,
            verb="confirm",
            user_id=people.a.id,
            version=event.payload.get("version"),
        )
        await actions.run(event.id, people.org)
        await actions.run(event.id, people.org)
        assert reach_fakes.NOTES == ["buy rice"]
        done = (
            await db_client.get_agent_event(event.id, organization_id=people.org)
        ).payload
        assert (
            done["state"] == "done"
            and done["done"]["note"] == "Done: create_note on Notes."
        )

    async def test_a_tool_that_says_it_sends_is_a_card_whatever_its_name(
        self, reach_on, people
    ):
        await connect_notes(people.org, people.a.id, reach_on.base)
        with acting_as(people.a.id):
            result = await decibyl._tool(
                people.org, call("ext_notes__get_and_share_digest"), people.a.id
            )
        assert result["status"] == "proposed"

    async def test_the_context_names_what_they_connected(self, reach_on, people):
        await connect_notes(people.org, people.a.id, reach_on.base)
        with acting_as(people.a.id):
            block = await outside_tools.context_block(people.org)
        assert "Notes: 3 tools (1 read-only)" in block
        assert "Zomato: not connected (a chip connects it)" in block
        assert "Swiggy: needs setup" in block
        with acting_as(people.b.id):
            assert "Notes" not in await outside_tools.context_block(people.org)


class TestTheChip:
    async def test_asking_for_a_tool_puts_a_chip_on_the_thread(self, reach_on, people):
        with acting_as(people.a.id):
            result = await decibyl._tool(
                people.org,
                call(
                    "connect_outside_tool",
                    name="Linear",
                    server_url="https://mcp.linear.app/mcp",
                    why="read issues",
                ),
                people.a.id,
            )
        assert result["status"] == "offered" and "NOT connected" in result["note"]
        rows = await db_client.agent_events(
            organization_id=people.org,
            kinds=["reach_connect_offered"],
            limit=5,
            assistant_thread=True,
            viewer_id=people.a.id,
        )
        chip = rows[0].payload
        assert chip["reach_kind"] == "tool" and chip["name"] == "Linear"
        assert chip["server_url"] == "https://mcp.linear.app/mcp"
        assert not decibyl._was_a_read(call("connect_outside_tool"), result)

    async def test_an_already_connected_tool_is_not_offered_again(
        self, reach_on, people
    ):
        await connect_notes(people.org, people.a.id, reach_on.base)
        with acting_as(people.a.id):
            result = await decibyl._tool(
                people.org, call("connect_outside_tool", name="notes"), people.a.id
            )
        assert result["status"] == "already_connected"

    async def test_the_rules_never_send_anybody_elsewhere(self, reach_on, people):
        prompt = decibyl.system_prompt(people.org)
        assert "Never send anybody to another screen" in prompt
