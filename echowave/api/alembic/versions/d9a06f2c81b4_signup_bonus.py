"""one signup bonus per organization

A partial unique index rather than a check in application code. Signup can be
retried, and two requests can race for the same brand-new organization — both
would find no bonus and both grant one. The index is the only place that
race can actually be settled.

Revision ID: d9a06f2c81b4
Revises: c8f31a604be7
Create Date: 2026-07-30 13:52:09.771204

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9a06f2c81b4"
down_revision: Union[str, None] = "c8f31a604be7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_credit_ledger_signup_bonus",
        "credit_ledger",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'trial' AND ref_type = 'signup_bonus'"),
    )


def downgrade() -> None:
    op.drop_index("uq_credit_ledger_signup_bonus", table_name="credit_ledger")
