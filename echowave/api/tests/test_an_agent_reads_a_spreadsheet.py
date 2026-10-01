"""U-3: any agent a person builds can read a spreadsheet whole.

One ``tables`` tool row on the workspace, put on a node like any other
tool, gives the agent Decibyl's own table tools -- describe, query, rank,
export -- and read_document for a long attachment. A brief that names a
spreadsheet attaches it unasked. A voice call gets none of them: a caller
is not ranking a sheet. With the flag off the row offers nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import ToolCategory
from api.services.documents import tools as document_tools
from api.services.workflow import agent_tables, tables
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

CTM = "api.services.workflow.pipecat_engine_custom_tools"
ALL = {*tables.NAMES, document_tools.READ}


def _row(uuid: str = "tbl-1"):
    return SimpleNamespace(
        tool_uuid=uuid,
        name=agent_tables.TOOL_NAME,
        description=agent_tables.DESCRIPTION,
        definition=agent_tables.definition(),
        category=ToolCategory.TABLES.value,
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
    with patch("api.constants.TABLE_TOOLS_ENABLED", True):
        yield


@pytest.fixture
def registered():
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


def _names(schemas) -> set[str]:
    return {getattr(s, "name", None) or s["name"] for s in schemas}


class TestTheToolsAnAgentIsOffered:
    @pytest.mark.asyncio
    async def test_a_text_run_gets_every_table_tool_and_the_reader(self, registered):
        manager = _manager(_engine(voice=False), registered, [_row()])
        assert _names(await manager.get_tool_schemas(["tbl-1"])) == ALL
        await manager.register_handlers(["tbl-1"])
        assert set(registered) == ALL

    @pytest.mark.asyncio
    async def test_a_voice_call_gets_none(self, registered):
        manager = _manager(_engine(voice=True), registered, [_row()])
        assert await manager.get_tool_schemas(["tbl-1"]) == []
        await manager.register_handlers(["tbl-1"])
        assert registered == {}

    @pytest.mark.asyncio
    async def test_the_flag_off_offers_nothing(self, registered):
        manager = _manager(_engine(voice=False), registered, [_row()])
        with patch("api.constants.TABLE_TOOLS_ENABLED", False):
            assert await manager.get_tool_schemas(["tbl-1"]) == []
            await manager.register_handlers(["tbl-1"])
        assert registered == {}


async def _call(registered, name, arguments):
    results = []

    async def result_callback(result, *a, **k):
        results.append(result)

    params = SimpleNamespace(
        arguments=arguments,
        tool_call_id="call-3",
        result_callback=result_callback,
        function_name=name,
    )
    with patch(f"{CTM}.app_interactions._safe_record", AsyncMock()):
        await registered[name](params)
    return results[0]


class TestACall:
    @pytest.mark.asyncio
    async def test_runs_for_the_runs_own_workspace_and_hands_files_to_the_run(
        self, registered
    ):
        manager = _manager(_engine(voice=False), registered, [_row()])
        await manager.register_handlers(["tbl-1"])
        run = AsyncMock(return_value={"status": "success", "rows": 3})
        with patch("api.services.workflow.tables.run", run):
            out = await _call(
                registered,
                tables.EXPORT_TOOL_NAME,
                {"file": "leads.xlsx", "title": "Top leads", "organization_id": 99},
            )
        assert out["status"] == "success"
        assert run.call_args.args[0] == tables.EXPORT_TOOL_NAME
        kwargs = run.call_args.kwargs
        assert kwargs["organization_id"] == 1
        assert kwargs["workflow_id"] == 42 and kwargs["workflow_run_id"] == 7

    @pytest.mark.asyncio
    async def test_read_document_reads_the_part_asked_for(self, registered):
        manager = _manager(_engine(voice=False), registered, [_row()])
        await manager.register_handlers(["tbl-1"])
        read = AsyncMock(return_value={"status": "success", "text": "..."})
        with patch("api.services.workflow.tables.read_document", read):
            await _call(
                registered, document_tools.READ, {"document": "Deck.pdf", "start": 9}
            )
        read.assert_awaited_once_with(1, {"document": "Deck.pdf", "start": 9})

    @pytest.mark.asyncio
    async def test_a_failure_is_told_not_raised(self, registered):
        manager = _manager(_engine(voice=False), registered, [_row()])
        await manager.register_handlers(["tbl-1"])
        with patch(
            "api.services.workflow.tables.run", AsyncMock(side_effect=RuntimeError("x"))
        ):
            out = await _call(registered, tables.RANK_TOOL_NAME, {"file": "x"})
        assert out["status"] == "error"


class TestTheWorkspaceRow:
    @pytest.mark.asyncio
    async def test_made_once_then_reused(self):
        db = SimpleNamespace(
            get_tools_for_organization=AsyncMock(return_value=[]),
            create_tool=AsyncMock(return_value=SimpleNamespace(tool_uuid="new")),
        )
        with patch("api.db.db_client", db):
            assert await agent_tables.ensure_tool(organization_id=1, user_id=5) == "new"
        created = db.create_tool.call_args.kwargs
        assert created["category"] == ToolCategory.TABLES.value
        assert created["definition"] == {"schema_version": 1, "type": "tables"}

        db.get_tools_for_organization = AsyncMock(return_value=[_row("had")])
        db.create_tool.reset_mock()
        with patch("api.db.db_client", db):
            assert await agent_tables.ensure_tool(organization_id=1, user_id=5) == "had"
        db.create_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_made_with_the_flag_off(self):
        db = SimpleNamespace(
            get_tools_for_organization=AsyncMock(return_value=[]),
            create_tool=AsyncMock(),
        )
        with (
            patch("api.constants.TABLE_TOOLS_ENABLED", False),
            patch("api.db.db_client", db),
        ):
            assert await agent_tables.ensure_tool(organization_id=1, user_id=5) is None
        db.create_tool.assert_not_called()


class TestABriefThatNamesASpreadsheet:
    @pytest.mark.parametrize(
        "spec, expected",
        [
            ("Rank the leads in my Excel file every Monday", True),
            ("Read the attached CSV of vendors and flag late ones", True),
            ("Keep our stock spreadsheet tidy", True),
            ("Compare the xlsx exports", True),
            ("Book a table for two at the restaurant", False),
            ("Summarise my Gmail every morning", False),
            ("An excellent receptionist", False),
        ],
    )
    def test_is_matched_on_whole_words(self, spec, expected):
        assert agent_tables.mentions_tables(spec) is expected

    @pytest.mark.asyncio
    async def test_attaches_the_row_to_the_calling_nodes(self):
        definition = {
            "nodes": [
                {"id": "a", "type": "startCall", "data": {}},
                {"id": "b", "type": "agentNode", "data": {"tool_uuids": ["x"]}},
            ]
        }
        with patch.object(agent_tables, "ensure_tool", AsyncMock(return_value="tbl-1")):
            out = await agent_tables.attach_if_named(
                definition, organization_id=1, user_id=5, spec="rank my Excel list"
            )
        by_id = {n["id"]: n for n in out["nodes"]}
        assert by_id["b"]["data"]["tool_uuids"] == ["x", "tbl-1"]

    @pytest.mark.asyncio
    async def test_a_brief_that_does_not_name_it_gets_nothing(self):
        ensure = AsyncMock(return_value="tbl-1")
        definition = {"nodes": [{"id": "b", "type": "agentNode", "data": {}}]}
        with patch.object(agent_tables, "ensure_tool", ensure):
            out = await agent_tables.attach_if_named(
                definition, organization_id=1, user_id=5, spec="Summarise my Gmail"
            )
        ensure.assert_not_called()
        assert out == definition

    @pytest.mark.asyncio
    async def test_a_row_that_cannot_be_made_costs_the_bot_nothing(self):
        definition = {"nodes": [{"id": "b", "type": "agentNode", "data": {}}]}
        with patch.object(
            agent_tables, "ensure_tool", AsyncMock(side_effect=RuntimeError("db"))
        ):
            out = await agent_tables.attach_if_named(
                definition, organization_id=1, user_id=5, spec="rank the CSV"
            )
        assert out == definition

    @pytest.mark.asyncio
    async def test_a_built_bot_gets_it_from_its_brief(self):
        from api.services.workflow import bot_from_brief

        assert "agent_tables.attach_if_named" in __import__("inspect").getsource(
            bot_from_brief
        )


class TestTheCategoryIsWhole:
    def test_the_readiness_checklist_has_nothing_to_connect(self):
        from api.services.workflow.readiness import connector_for

        assert connector_for(_row()) is None

    def test_it_can_be_created_through_the_request_schema(self):
        from api.schemas.tool import CreateToolRequest

        request = CreateToolRequest(
            name="Spreadsheets",
            category="tables",
            definition={"schema_version": 1, "type": "tables"},
        )
        assert request.definition.type == "tables"
