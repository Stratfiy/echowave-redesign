"""agency tier: who manages an account, and what they earn on it

An agency signs up its own clients and takes a flat commission on what those
clients are charged. It sees a roll-up of them and nothing else — it does not
sign in as a client, and cannot read their recordings or transcripts. Those are
conversations with the client's own customers, and an agency relationship is
not consent to listen to them.

``agency_commission_bps`` sits on the client rather than on the agency because
the rate is negotiated per deal. One rate per agency would make the first
exception a schema change.

``agency_commission_accruals`` snapshots what was earned each month rather than
deriving it on read. Derived, renegotiating a rate would silently rewrite every
past month — the same failure the rate card and the managed markup each avoid
by being effective-dated. Both ``charged_paise`` and ``bps_applied`` are stored
so a statement can show its own arithmetic; an amount on its own is a number
somebody has to trust.

The unique constraint on (agency, client, month) is what makes the monthly job
re-runnable. Without it a second run doubles what is owed.

Revision ID: d5a71b3c9e04
Revises: c4e93f0b28a7
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5a71b3c9e04"
down_revision: Union[str, Sequence[str], None] = "c4e93f0b28a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("managed_by_organization_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "organizations", sa.Column("agency_commission_bps", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_organizations_managed_by",
        "organizations",
        "organizations",
        ["managed_by_organization_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_organizations_managed_by",
        "organizations",
        ["managed_by_organization_id"],
    )

    op.create_table(
        "agency_commission_accruals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agency_organization_id", sa.Integer(), nullable=False),
        sa.Column("client_organization_id", sa.Integer(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("charged_paise", sa.BigInteger(), nullable=False),
        sa.Column("bps_applied", sa.Integer(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["agency_organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["client_organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agency_organization_id",
            "client_organization_id",
            "month",
            name="uq_agency_accrual_month",
        ),
    )
    op.create_index(
        op.f("ix_agency_commission_accruals_id"), "agency_commission_accruals", ["id"]
    )
    op.create_index(
        "ix_agency_accruals_agency",
        "agency_commission_accruals",
        ["agency_organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agency_accruals_agency", table_name="agency_commission_accruals")
    op.drop_index(
        op.f("ix_agency_commission_accruals_id"),
        table_name="agency_commission_accruals",
    )
    op.drop_table("agency_commission_accruals")

    op.drop_index("ix_organizations_managed_by", table_name="organizations")
    op.drop_constraint(
        "fk_organizations_managed_by", "organizations", type_="foreignkey"
    )
    op.drop_column("organizations", "agency_commission_bps")
    op.drop_column("organizations", "managed_by_organization_id")
