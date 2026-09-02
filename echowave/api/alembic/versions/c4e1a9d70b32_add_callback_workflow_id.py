"""add callback_workflow_id for missed call callback

Puts a number into callback mode: it is never answered, and the agent named
here rings the caller back instead. Nullable, so every existing number keeps
answering exactly as it does today — callback mode is opt-in per number.

ondelete SET NULL matches inbound_workflow_id. Deleting the agent must not
delete the phone number: the number may be printed on a customer's signage,
and the row is the only record that we hold it.

Revision ID: c4e1a9d70b32
Revises: a3f7c21e9b04
Create Date: 2026-09-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e1a9d70b32"
down_revision: Union[str, None] = "a3f7c21e9b04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telephony_phone_numbers",
        sa.Column("callback_workflow_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_telephony_phone_numbers_callback_workflow_id",
        "telephony_phone_numbers",
        "workflows",
        ["callback_workflow_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Partial: only the rows in callback mode are ever looked up this way, and
    # they are a small minority of numbers.
    op.create_index(
        "ix_telephony_phone_numbers_callback_workflow_id",
        "telephony_phone_numbers",
        ["callback_workflow_id"],
        postgresql_where=sa.text("callback_workflow_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telephony_phone_numbers_callback_workflow_id",
        table_name="telephony_phone_numbers",
    )
    op.drop_constraint(
        "fk_telephony_phone_numbers_callback_workflow_id",
        "telephony_phone_numbers",
        type_="foreignkey",
    )
    op.drop_column("telephony_phone_numbers", "callback_workflow_id")
