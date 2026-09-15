"""Deferred tool loading: a connected app is a name and a line until asked for.

Step 9's checks. The thread assistant is offered every connected tool by
name only, plus one ``load_tool``; loading returns the tool's argument
schema and the next round offers that tool in full. On a call, a Composio
tool with no declared arguments takes its schema from the cache when one is
known and is offered with its description alone when not, while the schema
is fetched behind the call. And the schema reader never raises.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.integrations.composio import schema as composio_schema
from api.services.workflow import connected_tools, decibyl
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

SEND = {
    "type": "object",
    "properties": {
        "recipient_email": {"type": "string", "description": "Who to send to."},
        "subject": {"type": "string", "description": "Subject line."},
        "body": {"type": "string", "description": "Plain-text body."},
    },
    "required": ["recipient_email", "body"],
}


def _tool(slug: str, name: str = "Gmail: send email", toolkit: str = "gmail"):
    return SimpleNamespace(
        id=1,
        tool_uuid="t-1",
        name=name,
        description="Send the customer a written confirmation.",
        category="composio",
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": toolkit, "parameters": []},
        },
    )


@pytest.fixture(autouse=True)
def _no_memory():
    composio_schema.forget()
    yield
    composio_schema.forget()


class TestTheIndex:
    def test_a_tool_not_loaded_is_a_name_and_a_line(self):
        tool = _tool("GMAIL_SEND_EMAIL")
        entries = connected_tools.schemas([tool])
        names = [e["name"] for e in entries]
        assert names == [
            connected_tools.LOAD_TOOL_NAME,
            connected_tools.function_name(tool),
        ]
        entry = entries[1]
        assert entry["parameters"] == {"type": "object", "properties": {}}
        assert connected_tools.LOAD_TOOL_NAME in entry["description"]

    def test_a_loaded_tool_is_offered_in_full(self):
        tool = _tool("GMAIL_SEND_EMAIL")
        fn = connected_tools.function_name(tool)
        entries = connected_tools.schemas([tool], {fn: SEND})
        assert entries[1]["parameters"] == SEND
        assert connected_tools.LOAD_TOOL_NAME not in entries[1]["description"]

    def test_no_tools_means_no_loader(self):
        assert connected_tools.schemas([]) == []

    def test_declared_parameters_still_win(self):
        tool = _tool("GMAIL_SEND_EMAIL")
        tool.definition["config"]["parameters"] = [
            {"name": "to", "type": "string", "description": "Address", "required": True}
        ]
        fn = connected_tools.function_name(tool)
        entry = connected_tools.schemas([tool], {fn: SEND})[1]
        assert set(entry["parameters"]["properties"]) == {"to"}

    def test_the_index_is_smaller_than_the_schemas(self):
        import json

        tools = [
            _tool(f"GMAIL_SEND_EMAIL_{i}", name=f"Gmail send {i}") for i in range(20)
        ]
        # A published Composio schema runs to a dozen described parameters;
        # three is the test fixture, not the field.
        published = {
            "type": "object",
            "properties": {
                f"field_{i}": {
                    "type": "string",
                    "description": "What this argument means, in one sentence.",
                }
                for i in range(12)
            },
            "required": ["field_0"],
        }
        loaded = {connected_tools.function_name(t): published for t in tools}
        full = len(json.dumps(connected_tools.schemas(tools, loaded)))
        index = len(json.dumps(connected_tools.schemas(tools)))
        assert index < full / 2


@pytest.mark.asyncio
class TestLoading:
    async def test_loading_reads_the_published_schema(self):
        tool = _tool("GMAIL_SEND_EMAIL")
        with patch.object(
            composio_schema, "input_schema", AsyncMock(return_value=SEND)
        ):
            result = await connected_tools.load(tool)
        assert result["status"] == "success"
        assert result["parameters"] == SEND
        assert result["tool"] == connected_tools.function_name(tool)

    async def test_an_unpublished_schema_is_a_note_not_an_error(self):
        tool = _tool("GMAIL_SEND_EMAIL")
        with patch.object(
            composio_schema, "input_schema", AsyncMock(return_value=None)
        ):
            result = await connected_tools.load(tool)
        assert result["status"] == "success"
        assert result["parameters"] == {"type": "object", "properties": {}}
        assert "description implies" in result["note"]


def _session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.mark.asyncio
class TestFromTheThread:
    async def test_load_then_call_two_hops_and_the_second_round_carries_the_schema(
        self,
    ):
        tool = _tool("GMAIL_FETCH_EMAILS", name="Gmail: fetch emails")
        fn = connected_tools.function_name(tool)
        asks_schema = ModelReply(
            text="",
            tool_calls=(
                ToolCall(
                    id="c1", name=connected_tools.LOAD_TOOL_NAME, arguments={"name": fn}
                ),
            ),
        )
        calls_tool = ModelReply(
            text="",
            tool_calls=(ToolCall(id="c2", name=fn, arguments={"query": "Meera"}),),
        )
        answers = ModelReply(text="Two mails from Meera.")
        stream = AsyncMock(side_effect=[asks_schema, calls_tool, answers])
        fetch = {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search."}},
            "required": [],
        }
        with (
            patch(
                "api.services.workflow.decibyl.build_context",
                new=AsyncMock(return_value="## Team\nnothing"),
            ),
            patch(
                "api.services.workflow.decibyl.office_context",
                new=AsyncMock(return_value=""),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.agent_events",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.async_session",
                return_value=_session(),
            ),
            patch(
                "api.services.agent_builder.settings.resolve_model",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        provider="openai", model="m", api_key="k"
                    )
                ),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ),
            patch("api.services.workflow.decibyl.reply_draft.clear", new=AsyncMock()),
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[tool])
            ),
            patch.object(
                composio_schema, "input_schema", AsyncMock(return_value=fetch)
            ),
            patch.object(
                connected_tools,
                "execute",
                AsyncMock(return_value={"status": "success", "data": {"count": 2}}),
            ) as run,
        ):
            body = await decibyl.answer(7, "any mail from Meera?")

        assert body == "Two mails from Meera."
        run.assert_awaited_once()
        first, second, third = stream.await_args_list
        # Round one: the tool by name only, and the loader.
        by_name = {t["name"]: t for t in first.kwargs["tools"]}
        assert connected_tools.LOAD_TOOL_NAME in by_name
        assert by_name[fn]["parameters"]["properties"] == {}
        # Round two, after the load: the tool with its arguments.
        by_name = {t["name"]: t for t in second.kwargs["tools"]}
        assert by_name[fn]["parameters"] == fetch
        # The load's reply reached the model as a tool result.
        sent = second.kwargs["conversation"].messages
        loads = [
            m for m in sent if m["role"] == "tool" and m["content"].get("tool") == fn
        ]
        assert loads and loads[0]["content"]["parameters"] == fetch
        # A read followed by a read: tools still offered on round three.
        assert third.kwargs["tools"] is not None

    async def test_loading_an_unknown_name_is_unavailable(self):
        loaded: dict = {}
        with patch.object(
            connected_tools, "list_for_organization", AsyncMock(return_value=[])
        ):
            result = await decibyl._load_tool(
                7,
                ToolCall(id="c", name="load_tool", arguments={"name": "app_nope"}),
                loaded,
            )
        assert result["status"] == "unavailable"
        assert loaded == {}


@pytest.mark.asyncio
class TestOnACall:
    """The engine never waits on a vendor for a schema: it reads the cache,
    and warms it for the next call when there is nothing there."""

    def _manager(self, tool):
        engine = Mock()
        engine._get_organization_id = AsyncMock(return_value=7)
        manager = CustomToolManager(engine)
        manager._tool_cache = {tool.tool_uuid: tool}
        return manager

    async def test_a_known_schema_gives_the_model_its_arguments(self):
        tool = _tool("GMAIL_SEND_EMAIL")
        manager = self._manager(tool)
        with (
            patch.object(composio_schema, "cached", AsyncMock(return_value=SEND)),
            patch.object(composio_schema, "warm") as warm,
        ):
            schemas = await manager.get_tool_schemas([tool.tool_uuid])
        assert set(schemas[0].properties) == {"recipient_email", "subject", "body"}
        assert schemas[0].required == ["recipient_email", "body"]
        warm.assert_not_called()

    async def test_an_unknown_schema_is_the_description_alone_and_a_warm(self):
        tool = _tool("GMAIL_SEND_EMAIL")
        manager = self._manager(tool)
        with (
            patch.object(composio_schema, "cached", AsyncMock(return_value=None)),
            patch.object(composio_schema, "warm") as warm,
        ):
            schemas = await manager.get_tool_schemas([tool.tool_uuid])
        assert schemas[0].properties == {}
        warm.assert_called_once_with("GMAIL_SEND_EMAIL")

    async def test_declared_parameters_are_never_replaced(self):
        tool = _tool("GMAIL_SEND_EMAIL")
        tool.definition["config"]["parameters"] = [
            {"name": "to", "type": "string", "description": "Address", "required": True}
        ]
        manager = self._manager(tool)
        with patch.object(
            composio_schema, "cached", AsyncMock(return_value=SEND)
        ) as read:
            schemas = await manager.get_tool_schemas([tool.tool_uuid])
        assert set(schemas[0].properties) == {"to"}
        read.assert_not_awaited()


class TestTheSchemaReader:
    def test_normalise_reads_composios_shape_and_trims_it(self):
        raw = {
            "slug": "GMAIL_SEND_EMAIL",
            "input_parameters": {
                "type": "object",
                "properties": {
                    "recipient_email": {
                        "type": "string",
                        "description": "x" * 1000,
                        "examples": ["a@b"],
                        "title": "Recipient",
                    },
                    "attachment": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "file_uploadable": True,
                    },
                },
                "required": ["recipient_email", "ghost"],
            },
        }
        schema = composio_schema.normalise(raw)
        assert schema["type"] == "object"
        assert set(schema["properties"]) == {"recipient_email", "attachment"}
        assert len(schema["properties"]["recipient_email"]["description"]) == (
            composio_schema.MAX_PARAMETER_DESCRIPTION
        )
        assert "examples" not in schema["properties"]["recipient_email"]
        assert "file_uploadable" not in schema["properties"]["attachment"]
        assert schema["required"] == ["recipient_email"]

    def test_normalise_refuses_a_shape_with_no_properties(self):
        assert composio_schema.normalise({"slug": "X"}) is None
        assert composio_schema.normalise("nope") is None

    def test_too_many_parameters_are_cut(self):
        raw = {"properties": {f"p{i}": {"type": "string"} for i in range(60)}}
        schema = composio_schema.normalise(raw)
        assert len(schema["properties"]) == composio_schema.MAX_PARAMETERS

    @pytest.mark.asyncio
    async def test_a_deployment_with_no_key_reads_nothing_and_never_raises(self):
        with (
            patch.object(composio_schema, "is_configured", return_value=False),
            patch.object(composio_schema, "_cache", AsyncMock(return_value=None)),
        ):
            assert await composio_schema.input_schema("GMAIL_SEND_EMAIL") is None

    @pytest.mark.asyncio
    async def test_a_fetch_is_shared_and_then_remembered(self):
        response = Mock(status_code=200)
        response.json.return_value = {"input_parameters": SEND}
        client = AsyncMock()
        client.get.return_value = response
        with (
            patch.object(composio_schema, "is_configured", return_value=True),
            patch.object(composio_schema, "_headers", return_value={}),
            patch.object(composio_schema, "_cache", AsyncMock(return_value=None)),
            patch("api.services.integrations.composio.schema.httpx.AsyncClient") as cls,
        ):
            cls.return_value.__aenter__.return_value = client
            first, second = await asyncio.gather(
                composio_schema.input_schema("GMAIL_SEND_EMAIL"),
                composio_schema.input_schema("gmail_send_email"),
            )
            third = await composio_schema.input_schema("GMAIL_SEND_EMAIL")
        assert first == second == third
        assert first["required"] == ["recipient_email", "body"]
        assert client.get.await_count == 1
        assert client.get.await_args.args[0].endswith(
            "/api/v3.1/tools/GMAIL_SEND_EMAIL"
        )

    @pytest.mark.asyncio
    async def test_a_vendor_error_is_none_and_not_remembered(self):
        response = Mock(status_code=500)
        response.json.return_value = {"error": "boom"}
        client = AsyncMock()
        client.get.return_value = response
        with (
            patch.object(composio_schema, "is_configured", return_value=True),
            patch.object(composio_schema, "_headers", return_value={}),
            patch.object(composio_schema, "_cache", AsyncMock(return_value=None)),
            patch("api.services.integrations.composio.schema.httpx.AsyncClient") as cls,
        ):
            cls.return_value.__aenter__.return_value = client
            assert await composio_schema.input_schema("GMAIL_SEND_EMAIL") is None
            assert await composio_schema.input_schema("GMAIL_SEND_EMAIL") is None
        assert client.get.await_count == 2
