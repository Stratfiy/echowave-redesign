"""A request never offers two tools by one name.

Seen on 23 Oct 2026: right after a workspace connected Google Sheets, the
next message to Decibyl came back "The assistant hit an error". Every model
provider refuses a request that offers two tools by one name, and refuses
the whole turn. Connected-app tools are named from their display names, and
nothing stopped two rows (an app connected twice, two actions whose names
sanitise alike) from arriving under one name.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import connected_tools, decibyl
from api.services.workflow.pipecat_engine_context_composer import unique_by_name
from api.services.workflow.pipecat_engine_custom_tools import get_function_schema


def _sheet_tool(name: str, slug: str):
    return SimpleNamespace(
        tool_uuid=slug.lower(),
        name=name,
        description=f"{name} in Google Sheets",
        category="integration",
        definition={
            "config": {"toolkit": "googlesheets", "tool_slug": slug, "parameters": []}
        },
    )


class TestUniqueByName:
    def test_the_first_of_each_name_is_kept_in_order(self):
        tools = [
            {"name": "a", "description": "first"},
            {"name": "b"},
            {"name": "a", "description": "second"},
        ]
        assert unique_by_name(tools) == [
            {"name": "a", "description": "first"},
            {"name": "b"},
        ]

    def test_function_schemas_are_read_by_their_name_too(self):
        one = get_function_schema("app_get_rows", "x", properties={}, required=[])
        two = get_function_schema("app_get_rows", "y", properties={}, required=[])
        assert unique_by_name([one, two]) == [one]

    def test_nothing_to_drop_is_left_as_it_was(self):
        tools = [{"name": "a"}, {"name": "b"}]
        assert unique_by_name(tools) == tools


@pytest.mark.asyncio
async def test_a_sheet_connected_twice_is_offered_to_decibyl_once():
    twice = [
        _sheet_tool("Get spreadsheet rows", "GOOGLESHEETS_GET_ROWS"),
        _sheet_tool("Get spreadsheet rows", "GOOGLESHEETS_GET_ROWS"),
        _sheet_tool("Get Spreadsheet Rows!", "GOOGLESHEETS_GET_ROWS_V2"),
    ]
    with patch.object(
        connected_tools, "list_for_organization", AsyncMock(return_value=twice)
    ):
        tools = await decibyl.tools_for(1)
    names = [t["name"] for t in tools]
    assert names.count("app_get_spreadsheet_rows") == 1
    assert len(names) == len(set(names))
