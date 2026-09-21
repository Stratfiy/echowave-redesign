"""An agent a person built can search the web (OP-1).

One ``web`` tool row on the workspace, put on a node like any other tool,
gives the agent Decibyl's own search and fetch: the same functions, the
same refusals, the same price, charged to the agent. A voice call gets
the search alone. A brief that names the web attaches it unasked. With
the flag off the row offers nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import ToolCategory
from api.services.workflow import agent_web, web_tools
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

CTM = "api.services.workflow.pipecat_engine_custom_tools"


def _web_row(uuid: str = "web-1"):
    return SimpleNamespace(
        tool_uuid=uuid,
        name=agent_web.TOOL_NAME,
        description=agent_web.DESCRIPTION,
        definition=agent_web.definition(),
        category=ToolCategory.WEB.value,
    )


def _engine(*, voice: bool):
    engine = SimpleNamespace()
    engine._is_voice = voice
    engine._workflow_run_id = 7
    engine.workflow = SimpleNamespace(id=42)
    engine.llm = SimpleNamespace(register_function=lambda *a, **k: None)
    engine._get_organization_id = AsyncMock(return_value=1)
    engine._get_workflow_id = AsyncMock(return_value=42)
    return engine


@pytest.fixture(autouse=True)
def _flag_on():
    with patch("api.constants.DECIBYL_TOOLS_2026_09_ENABLED", True):
        yield


@pytest.fixture
def registered():
    """Whatever the manager registers on the LLM, by function name."""
    return {}


def _manager(engine, registered, rows):
    manager = CustomToolManager(engine)
    manager._writes_gated = False
    manager._attribution = {
        "organization_id": 1,
        "workflow_id": 42,
        "workflow_run_id": 7,
        "workflow_version_id": None,
    }
    engine.llm.register_function = lambda name, handler, **kw: registered.__setitem__(
        name, handler
    )
    manager._load_tools = AsyncMock(return_value=rows)
    return manager


class TestTheToolsAnAgentIsOffered:
    @pytest.mark.asyncio
    async def test_a_text_run_gets_search_and_fetch(self, registered):
        manager = _manager(_engine(voice=False), registered, [_web_row()])
        schemas = await manager.get_tool_schemas(["web-1"])
        assert [s.name for s in schemas] == [
            web_tools.SEARCH_TOOL_NAME,
            web_tools.FETCH_TOOL_NAME,
        ]
        await manager.register_handlers(["web-1"])
        assert set(registered) == {
            web_tools.SEARCH_TOOL_NAME,
            web_tools.FETCH_TOOL_NAME,
        }

    @pytest.mark.asyncio
    async def test_a_voice_call_gets_search_only(self, registered):
        manager = _manager(_engine(voice=True), registered, [_web_row()])
        schemas = await manager.get_tool_schemas(["web-1"])
        assert [s.name for s in schemas] == [web_tools.SEARCH_TOOL_NAME]
        await manager.register_handlers(["web-1"])
        assert set(registered) == {web_tools.SEARCH_TOOL_NAME}

    @pytest.mark.asyncio
    async def test_the_flag_off_offers_nothing(self, registered):
        with patch("api.constants.DECIBYL_TOOLS_2026_09_ENABLED", False):
            manager = _manager(_engine(voice=False), registered, [_web_row()])
            assert await manager.get_tool_schemas(["web-1"]) == []
            await manager.register_handlers(["web-1"])
            assert registered == {}

    @pytest.mark.asyncio
    async def test_the_web_sits_beside_other_tools(self, registered):
        calculator = SimpleNamespace(
            tool_uuid="calc-1",
            name="Calculator",
            description="",
            definition={"type": "calculator"},
            category=ToolCategory.CALCULATOR.value,
        )
        manager = _manager(_engine(voice=False), registered, [calculator, _web_row()])
        schemas = await manager.get_tool_schemas(["calc-1", "web-1"])
        assert [s.name for s in schemas] == [
            "safe_calculator",
            web_tools.SEARCH_TOOL_NAME,
            web_tools.FETCH_TOOL_NAME,
        ]


class TestASearchOnARun:
    @pytest.mark.asyncio
    async def test_is_charged_to_the_agent_and_keyed_on_the_call(self, registered):
        manager = _manager(_engine(voice=False), registered, [_web_row()])
        await manager.register_handlers(["web-1"])
        handler = registered[web_tools.SEARCH_TOOL_NAME]

        results = []

        async def result_callback(result, *a, **k):
            results.append(result)

        params = SimpleNamespace(
            arguments={"query": "dental clinics in Chennai"},
            tool_call_id="call-9",
            result_callback=result_callback,
            function_name=web_tools.SEARCH_TOOL_NAME,
        )
        search = AsyncMock(return_value={"status": "success", "results": []})
        with (
            patch("api.services.workflow.web_tools.search", search),
            patch(f"{CTM}.app_interactions._safe_record", AsyncMock()),
        ):
            await handler(params)

        assert results and results[0]["status"] == "success"
        kwargs = search.call_args.kwargs
        assert search.call_args.args[0] == 1
        assert kwargs["workflow_id"] == 42
        assert kwargs["ref_id"] == "7:web:call-9"

    @pytest.mark.asyncio
    async def test_a_search_that_blows_up_is_told_not_raised(self, registered):
        manager = _manager(_engine(voice=False), registered, [_web_row()])
        await manager.register_handlers(["web-1"])
        handler = registered[web_tools.SEARCH_TOOL_NAME]
        results = []

        async def result_callback(result, *a, **k):
            results.append(result)

        params = SimpleNamespace(
            arguments={"query": "x"},
            tool_call_id="c",
            result_callback=result_callback,
            function_name=web_tools.SEARCH_TOOL_NAME,
        )
        with (
            patch(
                "api.services.workflow.web_tools.search",
                AsyncMock(side_effect=RuntimeError("vendor down")),
            ),
            patch(f"{CTM}.app_interactions._safe_record", AsyncMock()),
        ):
            await handler(params)
        assert results[0]["status"] == "error"

    @pytest.mark.asyncio
    async def test_the_fetch_refuses_what_decibyl_refuses(self, registered):
        """Same function, same rule: no LinkedIn, ever."""
        manager = _manager(_engine(voice=False), registered, [_web_row()])
        await manager.register_handlers(["web-1"])
        handler = registered[web_tools.FETCH_TOOL_NAME]
        results = []

        async def result_callback(result, *a, **k):
            results.append(result)

        params = SimpleNamespace(
            arguments={"url": "https://www.linkedin.com/in/somebody"},
            tool_call_id="c",
            result_callback=result_callback,
            function_name=web_tools.FETCH_TOOL_NAME,
        )
        with patch(f"{CTM}.app_interactions._safe_record", AsyncMock()):
            await handler(params)
        assert results[0]["status"] != "success"
        assert "linkedin" in str(results[0]).lower()


class TestTheWorkspaceRow:
    @pytest.mark.asyncio
    async def test_made_once_then_reused(self):
        db = SimpleNamespace(
            get_tools_for_organization=AsyncMock(return_value=[]),
            create_tool=AsyncMock(return_value=SimpleNamespace(tool_uuid="new-web")),
        )
        with patch("api.db.db_client", db):
            assert (
                await agent_web.ensure_tool(organization_id=1, user_id=5) == "new-web"
            )
        created = db.create_tool.call_args.kwargs
        assert created["category"] == ToolCategory.WEB.value
        assert created["definition"] == {"schema_version": 1, "type": "web"}
        assert created["user_id"] == 5

        db.get_tools_for_organization = AsyncMock(return_value=[_web_row("had-one")])
        db.create_tool.reset_mock()
        with patch("api.db.db_client", db):
            assert (
                await agent_web.ensure_tool(organization_id=1, user_id=5) == "had-one"
            )
        db.create_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_made_with_the_flag_off(self):
        db = SimpleNamespace(
            get_tools_for_organization=AsyncMock(return_value=[]),
            create_tool=AsyncMock(),
        )
        with (
            patch("api.constants.DECIBYL_TOOLS_2026_09_ENABLED", False),
            patch("api.db.db_client", db),
        ):
            assert await agent_web.ensure_tool(organization_id=1, user_id=5) is None
        db.create_tool.assert_not_called()


class TestABriefThatNamesTheWeb:
    @pytest.mark.parametrize(
        "spec, expected",
        [
            ("Find dental clinics online and draft an email to each", True),
            ("Research each lead on the web before writing", True),
            ("Look up the company's website and note what it sells", True),
            ("Summarise my Gmail every morning", False),
            ("A cobweb-themed party planner", False),
        ],
    )
    def test_is_matched_on_whole_words(self, spec, expected):
        assert agent_web.mentions_web(spec) is expected

    @pytest.mark.asyncio
    async def test_attaches_the_row_to_the_calling_nodes(self):
        definition = {
            "nodes": [
                {"id": "a", "type": "startCall", "data": {}},
                {"id": "b", "type": "agentNode", "data": {"tool_uuids": ["x"]}},
                {"id": "c", "type": "endCall", "data": {}},
            ]
        }
        with patch(
            "api.services.workflow.agent_web.ensure_tool",
            AsyncMock(return_value="web-1"),
        ):
            out = await agent_web.attach_if_named(
                definition,
                organization_id=1,
                user_id=5,
                spec="Research each lead online, then draft an email",
            )
        by_id = {n["id"]: n for n in out["nodes"]}
        assert "web-1" in by_id["b"]["data"]["tool_uuids"]
        assert by_id["b"]["data"]["tool_uuids"][0] == "x"

    @pytest.mark.asyncio
    async def test_a_brief_that_does_not_name_it_gets_nothing(self):
        ensure = AsyncMock(return_value="web-1")
        definition = {"nodes": [{"id": "b", "type": "agentNode", "data": {}}]}
        with patch("api.services.workflow.agent_web.ensure_tool", ensure):
            out = await agent_web.attach_if_named(
                definition, organization_id=1, user_id=5, spec="Summarise my Gmail"
            )
        ensure.assert_not_called()
        assert out["nodes"][0]["data"] == {}

    @pytest.mark.asyncio
    async def test_a_row_that_cannot_be_made_costs_the_bot_nothing(self):
        definition = {"nodes": [{"id": "b", "type": "agentNode", "data": {}}]}
        with patch(
            "api.services.workflow.agent_web.ensure_tool",
            AsyncMock(side_effect=RuntimeError("db away")),
        ):
            out = await agent_web.attach_if_named(
                definition, organization_id=1, user_id=5, spec="search the web"
            )
        assert out == definition


class TestTheCategoryIsWhole:
    def test_the_readiness_checklist_has_nothing_to_connect(self):
        from api.services.workflow.readiness import connector_for

        assert connector_for(_web_row()) is None

    def test_a_web_tool_can_be_created_through_the_request_schema(self):
        from api.schemas.tool import CreateToolRequest

        request = CreateToolRequest(
            name="Web search",
            category="web",
            definition={"schema_version": 1, "type": "web"},
        )
        assert request.definition.type == "web"
