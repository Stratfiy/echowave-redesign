"""tables in ToolCategory (U-3)

Revision ID: b5e9c3a7d1f4
Revises: 202610010100kan277
Create Date: 2026-09-30

Any agent a person builds can read a spreadsheet whole: one built-in tool
row of category ``tables`` per workspace, like ``web``.
"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

revision: str = "b5e9c3a7d1f4"
down_revision: Union[str, None] = "202610010100kan277"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WITH_TABLES = [
    "http_api",
    "end_call",
    "transfer_call",
    "calculator",
    "native",
    "integration",
    "mcp",
    "google_calendar",
    "rate_table",
    "composio",
    "web",
    "team_calls",
    "tables",
]
_WITHOUT = [value for value in _WITH_TABLES if value != "tables"]
_AFFECTED = [
    TableReference(table_schema="public", table_name="tools", column_name="category")
]


def upgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITH_TABLES,
        affected_columns=_AFFECTED,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    op.execute("DELETE FROM tools WHERE category = 'tables'")
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITHOUT,
        affected_columns=_AFFECTED,
        enum_values_to_rename=[],
    )
