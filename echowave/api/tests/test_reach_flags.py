"""Stream `reach`: off by default, and off means today's behaviour.

Every new thing is behind ``outside_tools``, ``ordering`` or
``price_compare``. With them off: the routes are 404s, Decibyl is handed
none of the new tools and told none of the new rules, its thread reads no
new kinds, and nothing about a person's connections reaches the context.
"""

from __future__ import annotations

import pytest

from api import constants
from api.services import features
from api.services.workflow import decibyl
from api.tests.support.reach_fixtures import (  # noqa: F401
    acting_as,
    client_as,
    fake_servers,
    people,
    reach_off,
    reach_on,
)

NEW = ("outside_tools", "ordering", "price_compare")


class TestRegistered:
    def test_each_flag_is_registered_and_described(self):
        for name in NEW:
            assert name in features.FLAGS
            assert features.DESCRIPTIONS.get(name)

    def test_each_is_off_by_default(self):
        for name in NEW:
            assert getattr(constants, features.FLAGS[name]) is False

    def test_the_ui_knows_each_flag(self):
        from pathlib import Path

        text = (
            Path(constants.APP_ROOT_DIR).parent / "ui/src/lib/features.ts"
        ).read_text()
        for name in NEW:
            assert f'"{name}"' in text


@pytest.mark.asyncio
class TestOffIsToday:
    async def test_routes_are_not_there(self, reach_off, people):
        async with client_as(people.a) as client:
            for path in ("/api/v1/reach/connections", "/api/v1/reach/providers"):
                assert (await client.get(path)).status_code == 404
            response = await client.post(
                "/api/v1/reach/connections",
                json={
                    "kind": "tool",
                    "name": "x",
                    "server_url": "https://example.com/mcp",
                },
            )
            assert response.status_code == 404

    async def test_decibyl_gets_no_new_tool_rule_or_kind(self, reach_off, people):
        with acting_as(people.a.id):
            tools = await decibyl.tools_for(people.org, {})
        names = {t["name"] for t in tools}
        assert not {n for n in names if n.startswith("ext_")}
        assert not names & {
            "connect_outside_tool",
            "order_search",
            "order_prepare",
            "compare_prices",
        }
        prompt = decibyl.system_prompt(people.org)
        assert "order_prepare" not in prompt and "connect_outside_tool" not in prompt
        kinds = decibyl.thread_filter(people.org)["kinds"]
        assert "reach_connect_offered" not in kinds and "reach_comparison" not in kinds

    async def test_on_the_same_turn_has_them(self, reach_on, people):
        """What must appear, not only what must not."""
        with acting_as(people.a.id):
            names = {t["name"] for t in await decibyl.tools_for(people.org, {})}
        assert {
            "connect_outside_tool",
            "order_search",
            "order_prepare",
            "compare_prices",
        } <= names
        assert "order_prepare" in decibyl.system_prompt(people.org)
        kinds = decibyl.thread_filter(people.org)["kinds"]
        assert "reach_connect_offered" in kinds and "reach_comparison" in kinds

    async def test_a_turn_with_no_person_has_none(self, reach_on, people):
        """A routine or background run reaches nobody's account."""
        names = {t["name"] for t in await decibyl.tools_for(people.org, {})}
        assert "order_prepare" not in names and "connect_outside_tool" not in names
