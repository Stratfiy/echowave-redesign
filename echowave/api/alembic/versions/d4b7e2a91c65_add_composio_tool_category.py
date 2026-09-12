"""add composio in ToolCategory

Revision ID: d4b7e2a91c65
Revises: b8e4d1f60a92
Create Date: 2026-09-11 22:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = "d4b7e2a91c65"
down_revision: Union[str, None] = "b8e4d1f60a92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WITH_COMPOSIO = [
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
]
_WITHOUT_COMPOSIO = [value for value in _WITH_COMPOSIO if value != "composio"]
_AFFECTED = [
    TableReference(table_schema="public", table_name="tools", column_name="category")
]


def upgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITH_COMPOSIO,
        affected_columns=_AFFECTED,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    # Rows using the value being removed are deleted rather than left to break
    # the enum change, which is what the value existing at all implies: a
    # downgrade past this point is a deployment that cannot execute Composio
    # tools, and a tool row it cannot execute is worse kept than dropped.
    op.execute("DELETE FROM tools WHERE category = 'composio'")
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITHOUT_COMPOSIO,
        affected_columns=_AFFECTED,
        enum_values_to_rename=[],
    )
