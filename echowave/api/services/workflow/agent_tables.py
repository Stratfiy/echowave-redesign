"""Spreadsheets on any agent (U-3): one tool row, Decibyl's table tools.

The table tools (``tables``) read an attached Excel or CSV file whole --
describe, query, rank, export -- and ``read_document`` reads a long
attachment a part at a time. Decibyl has had them in its thread; this
gives the same functions to every agent a person builds, because a
weekly "re-rank my pipeline sheet" routine or a vendor-list checker has
the same job and no Decibyl in the loop.

**One row on the workspace**, in the web tool's pattern (``agent_web``):
a ``tools`` row of category ``tables`` with nothing to configure, put on
a node's ``tool_uuids``. ``ensure_tool`` makes it the first time
something needs it, and a brief that names a spreadsheet attaches it.

**Text runs only.** A caller on the phone is not ranking a sheet, and a
table's worth of rows read into a voice context is a wall of words.

**The run's own workspace, the run's own thread.** The organization comes
from the run, never from the model's arguments; an exported workbook is
handed over to the run that made it. Behind TABLE_TOOLS_ENABLED.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from api.enums import ToolCategory, ToolStatus
from api.services.workflow import tables

TOOL_NAME = "Spreadsheets"
DESCRIPTION = (
    "Read an attached Excel or CSV file whole -- filter, rank and export it "
    "-- and read a long document a part at a time."
)

#: Words a brief uses when it means a spreadsheet. Whole words: "table"
#: and "sheet" are left out, because a restaurant books a table and a
#: Google Sheet is a connected app of its own.
TABLE_WORDS = (
    "spreadsheet",
    "spreadsheets",
    "excel",
    "csv",
    "csvs",
    "xlsx",
    "workbook",
)


def enabled() -> bool:
    return tables.enabled()


def definition() -> dict[str, Any]:
    return {"schema_version": 1, "type": ToolCategory.TABLES.value}


def is_tables_tool(tool: Any) -> bool:
    return getattr(tool, "category", None) == ToolCategory.TABLES.value


def function_schemas(*, voice: bool) -> list[dict[str, Any]]:
    """The raw ``{"type": "function", ...}`` schemas: every table tool and
    read_document on a text run, nothing on a voice call."""
    if voice:
        return []
    from api.services.documents import tools as document_tools

    raw = [*tables.schemas(), document_tools.read_schema()]
    return [{"type": "function", "function": schema} for schema in raw]


def names() -> frozenset[str]:
    from api.services.documents import tools as document_tools

    return frozenset({*tables.NAMES, document_tools.READ})


def mentions_tables(spec: str) -> bool:
    """Whether a brief names a spreadsheet, on whole words."""
    text = (spec or "").lower()
    return bool(text) and any(
        re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text)
        for word in TABLE_WORDS
    )


async def ensure_tool(*, organization_id: int, user_id: int) -> str | None:
    """The workspace's spreadsheet tool row, made if it has none. Its uuid,
    or None when the flag is off."""
    if not enabled():
        return None
    from api.db import db_client

    existing = await db_client.get_tools_for_organization(
        organization_id,
        status=ToolStatus.ACTIVE.value,
        category=ToolCategory.TABLES.value,
    )
    for row in existing:
        return str(row.tool_uuid)
    created = await db_client.create_tool(
        organization_id=organization_id,
        user_id=user_id,
        name=TOOL_NAME,
        definition=definition(),
        category=ToolCategory.TABLES.value,
        description=DESCRIPTION,
        icon="table",
        icon_color="#059669",
    )
    return str(created.tool_uuid)


async def attach_if_named(
    definition_: dict[str, Any],
    *,
    organization_id: int,
    user_id: int,
    spec: str,
) -> dict[str, Any]:
    """Put the spreadsheet tool on a built bot's calling nodes when its
    brief names one. Never raises: the bot is the deliverable."""
    if not enabled() or not mentions_tables(spec):
        return definition_
    try:
        uuid = await ensure_tool(organization_id=organization_id, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not give the built agent the spreadsheet tools: {}", exc)
        return definition_
    if not uuid:
        return definition_
    from api.services.workflow import brief_apps

    logger.info("Built agent gets the spreadsheet tools: its brief names one")
    return brief_apps.attach(definition_, [uuid])


async def call(
    name: str,
    arguments: dict[str, Any],
    *,
    organization_id: int,
    workflow_id: int | None,
    workflow_run_id: int | None,
) -> dict[str, Any]:
    """One call from an agent's run. Never raises."""
    from api.services.documents import tools as document_tools

    try:
        if name == document_tools.READ:
            return await tables.read_document(organization_id, arguments)
        return await tables.run(
            name,
            organization_id=organization_id,
            arguments=arguments,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
        )
    except Exception as exc:  # noqa: BLE001 - the turn must finish
        logger.error("{} failed on a run: {}", name, exc)
        return {"status": "error", "error": "That spreadsheet could not be read."}


__all__ = [
    "DESCRIPTION",
    "TABLE_WORDS",
    "TOOL_NAME",
    "attach_if_named",
    "call",
    "definition",
    "enabled",
    "ensure_tool",
    "function_schemas",
    "is_tables_tool",
    "mentions_tables",
    "names",
]
