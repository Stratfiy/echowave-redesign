"""team_calls in ToolCategory (CR-3)

Revision ID: a4d8f1c7e2b5
Revises: e3a7c5b9d2f1
Create Date: 2026-09-22

The telecaller coach's one tool: a read of the team's imported dialer calls.
"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

revision: str = "a4d8f1c7e2b5"
down_revision: Union[str, None] = "e3a7c5b9d2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WITH_TEAM_CALLS = [
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
]
_WITHOUT = [value for value in _WITH_TEAM_CALLS if value != "team_calls"]
_AFFECTED = [
    TableReference(table_schema="public", table_name="tools", column_name="category")
]


def upgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITH_TEAM_CALLS,
        affected_columns=_AFFECTED,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    op.execute("DELETE FROM tools WHERE category = 'team_calls'")
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITHOUT,
        affected_columns=_AFFECTED,
        enum_values_to_rename=[],
    )
