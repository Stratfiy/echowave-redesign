"""contacts may be email-only (OP-3)

Revision ID: a7c0e3f6b9d2
Revises: f6b9d2e5a8c3
Create Date: 2026-09-21

A prospect found on the web has an address and no number. The phone
columns become optional, an address is stored beside them, and a check
keeps every contact reachable one way or the other.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c0e3f6b9d2"
down_revision: str | None = "f6b9d2e5a8c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "contacts", "phone_raw", existing_type=sa.String(255), nullable=True
    )
    op.alter_column(
        "contacts", "phone_normalized", existing_type=sa.String(255), nullable=True
    )
    op.add_column("contacts", sa.Column("email", sa.String(320), nullable=True))
    op.add_column(
        "contacts", sa.Column("email_normalized", sa.String(320), nullable=True)
    )
    op.create_unique_constraint(
        "uq_contacts_list_email", "contacts", ["contact_list_id", "email_normalized"]
    )
    op.create_check_constraint(
        "ck_contacts_reachable",
        "contacts",
        "phone_normalized IS NOT NULL OR email_normalized IS NOT NULL",
    )
    op.create_index(
        "ix_contacts_email", "contacts", ["contact_list_id", "email_normalized"]
    )


def downgrade() -> None:
    op.execute("DELETE FROM contacts WHERE phone_normalized IS NULL")
    op.drop_index("ix_contacts_email", table_name="contacts")
    op.drop_constraint("ck_contacts_reachable", "contacts", type_="check")
    op.drop_constraint("uq_contacts_list_email", "contacts", type_="unique")
    op.drop_column("contacts", "email_normalized")
    op.drop_column("contacts", "email")
    op.alter_column(
        "contacts", "phone_normalized", existing_type=sa.String(255), nullable=False
    )
    op.alter_column(
        "contacts", "phone_raw", existing_type=sa.String(255), nullable=False
    )
