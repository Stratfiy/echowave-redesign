"""An MCP server is connected too, and the bot builder could not see it.

Zerodha is not in Composio's catalogue, so Kite reaches Decibyl the other
way: an MCP tool pointing at the hosted server, created on the account and
attached to a bot by hand. It works -- the bot discovered 22 tools and
called them.

Nothing else knew it existed. ``brief_apps.named`` matches Composio toolkit
slugs, so a brief saying "Zerodha" attached nothing and the tool had to be
put on the node by hand. And ``list_for_organization`` ends with
``[t for t in rows if is_connected(t)]``, where ``is_connected`` is
Composio-only, so the whole MCP row was filtered out before the context
block ever counted it. Asked to build a Zerodha bot, Decibyl answered that
Zerodha "isn't an app I can connect from here -- there's no such connector
available in this workspace", with a working Kite tool sitting on the
account.

Two different fixes, because the two halves are not the same problem.
A bot **can** execute an MCP tool (``pipecat_engine_custom_tools`` has the
branch), so the builder attaching one is a real capability. Decibyl
**cannot** -- its execute path is Composio's -- so naming one in its tool
list would offer a tool it cannot run, which is the bug this file exists to
end, pointed the other way. It gets the truth instead: the app is connected
and bots can use it.
"""

from __future__ import annotations

from types import SimpleNamespace

from api.enums import ToolCategory
from api.services.workflow import brief_apps, connected_tools


def mcp_tool(name: str, *, uuid: str = "u-mcp") -> SimpleNamespace:
    return SimpleNamespace(
        tool_uuid=uuid,
        name=name,
        description=f"{name} over MCP",
        status="active",
        category=ToolCategory.MCP.value,
        definition={
            "type": "mcp",
            "config": {"url": "https://mcp.kite.trade/mcp", "tools_filter": []},
        },
    )


def composio(slug: str, *, uuid: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        tool_uuid=uuid or f"u-{slug.lower()}",
        name=slug,
        description=slug,
        status="active",
        category=ToolCategory.COMPOSIO.value,
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": slug.split("_")[0].lower()},
        },
    )


class TestABriefThatNamesAnMcpServer:
    def test_naming_it_attaches_it(self):
        """The brief said Zerodha and got nothing."""
        tools = [mcp_tool("Zerodha Kite")]
        assert brief_apps.tool_uuids(
            "every morning report my Zerodha holdings", tools
        ) == ["u-mcp"]

    def test_the_match_is_on_the_tools_own_name(self):
        tools = [mcp_tool("Zerodha Kite")]
        assert brief_apps.tool_uuids("read my kite portfolio", tools) == ["u-mcp"]

    def test_a_brief_naming_nothing_attaches_nothing(self):
        tools = [mcp_tool("Zerodha Kite")]
        assert brief_apps.tool_uuids("answer the phone politely", tools) == []

    def test_a_whole_word_only(self):
        """ "kiteboarding lessons" is not a brokerage."""
        tools = [mcp_tool("Zerodha Kite")]
        assert brief_apps.tool_uuids("we sell kiteboarding lessons", tools) == []

    def test_it_sits_beside_the_composio_ones(self):
        tools = [mcp_tool("Zerodha Kite"), composio("GMAIL_FETCH_EMAILS")]
        kept = brief_apps.tool_uuids("read gmail and my zerodha holdings", tools)
        assert "u-mcp" in kept
        assert "u-gmail_fetch_emails" in kept


class TestWhatDecibylIsTold:
    def test_an_mcp_app_is_not_offered_as_a_tool_it_can_run(self):
        """Decibyl executes Composio. Naming an MCP tool in its own list
        would be the drawer lying again, in the other direction."""
        rows = [mcp_tool("Zerodha Kite"), composio("GMAIL_FETCH_EMAILS")]
        runnable = [t for t in rows if connected_tools.is_connected(t)]
        assert [t.name for t in runnable] == ["GMAIL_FETCH_EMAILS"]

    def test_but_it_is_named_as_connected(self):
        """So it stops saying the app is not connected here."""
        block = connected_tools.apps_block(
            [composio("GMAIL_FETCH_EMAILS")], mcp_servers=["Zerodha Kite"]
        )
        assert "Zerodha Kite" in block

    def test_and_says_a_bot_is_what_uses_it(self):
        block = connected_tools.apps_block(
            [composio("GMAIL_FETCH_EMAILS")], mcp_servers=["Zerodha Kite"]
        )
        assert "bot" in block.lower()

    def test_no_mcp_servers_leaves_the_block_alone(self):
        plain = connected_tools.apps_block([composio("GMAIL_FETCH_EMAILS")])
        assert "Zerodha" not in plain

    def test_an_account_with_only_an_mcp_server_is_not_empty_handed(self):
        """ "No apps connected" was the old answer, and it was false."""
        block = connected_tools.apps_block([], mcp_servers=["Zerodha Kite"])
        assert "Zerodha Kite" in block


class TestItIsActuallyWiredIn:
    """Both halves read their tools from somewhere, and both were reading
    from ``list_for_organization`` -- which filters to Composio. A module
    that can see an MCP row is worth nothing if nothing hands it one."""

    async def test_the_builder_is_given_the_mcp_rows(self):
        from unittest.mock import AsyncMock, patch

        from api.services.workflow import bot_from_brief

        definition = {
            "nodes": [{"id": "start-1", "type": "startCall", "data": {}}],
            "edges": [],
        }
        with (
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
            patch.object(
                connected_tools,
                "mcp_for_organization",
                AsyncMock(return_value=[mcp_tool("Zerodha Kite")]),
            ),
        ):
            out = await bot_from_brief._attach_named_apps(
                definition,
                organization_id=1,
                spec="report my Zerodha holdings each morning",
            )
        assert out["nodes"][0]["data"]["tool_uuids"] == ["u-mcp"]

    async def test_a_brief_naming_no_server_still_attaches_nothing(self):
        from unittest.mock import AsyncMock, patch

        from api.services.workflow import bot_from_brief

        definition = {
            "nodes": [{"id": "start-1", "type": "startCall", "data": {}}],
            "edges": [],
        }
        with (
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
            patch.object(
                connected_tools,
                "mcp_for_organization",
                AsyncMock(return_value=[mcp_tool("Zerodha Kite")]),
            ),
        ):
            out = await bot_from_brief._attach_named_apps(
                definition, organization_id=1, spec="answer the phone politely"
            )
        assert not (out["nodes"][0].get("data") or {}).get("tool_uuids")
