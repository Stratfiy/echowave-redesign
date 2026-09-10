"""add rate_table in ToolCategory

Revision ID: a3f7c21e9d40
Revises: d7e1a4c9b2f0
Create Date: 2026-09-10 20:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = "a3f7c21e9d40"
down_revision: Union[str, None] = "d7e1a4c9b2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WITH_RATE_TABLE = [
    "http_api",
    "end_call",
    "transfer_call",
    "calculator",
    "native",
    "integration",
    "mcp",
    "google_calendar",
    "rate_table",
]
_WITHOUT_RATE_TABLE = [value for value in _WITH_RATE_TABLE if value != "rate_table"]
_AFFECTED = [
    TableReference(table_schema="public", table_name="tools", column_name="category")
]


def upgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITH_RATE_TABLE,
        affected_columns=_AFFECTED,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITHOUT_RATE_TABLE,
        affected_columns=_AFFECTED,
        enum_values_to_rename=[],
    )
