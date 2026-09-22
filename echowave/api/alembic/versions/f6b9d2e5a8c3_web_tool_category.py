"""web in ToolCategory (OP-1)

Revision ID: f6b9d2e5a8c3
Revises: e5a8c1d4f7b2
Create Date: 2026-09-21

"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

revision: str = "f6b9d2e5a8c3"
down_revision: Union[str, None] = "e5a8c1d4f7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WITH_WEB = [
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
]
_WITHOUT_WEB = [value for value in _WITH_WEB if value != "web"]
_AFFECTED = [
    TableReference(table_schema="public", table_name="tools", column_name="category")
]


def upgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITH_WEB,
        affected_columns=_AFFECTED,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    op.execute("DELETE FROM tools WHERE category = 'web'")
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITHOUT_WEB,
        affected_columns=_AFFECTED,
        enum_values_to_rename=[],
    )
