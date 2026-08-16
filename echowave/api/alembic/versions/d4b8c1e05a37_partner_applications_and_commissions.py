"""Partner applications, and effective-dated commission

A partner account is an ordinary account with a commercial arrangement
attached, not a different product, so this adds a way to *ask* rather than a
second signup: four questions, a staff decision, and a commission recorded
against the application it was granted on.

Two tables rather than columns on ``organizations``:

- ``partner_applications`` keeps the decision. "Why is this account on 12%?"
  is asked months later and the answer is the application it was granted
  against. Rejections stay so a second application can be read next to the
  first.
- ``partner_commissions`` is effective-dated, exactly like
  ``organization_rate_history``. A commission is never updated in place;
  changing one closes the open row and inserts a new one, so a statement for
  March reproduces March's number after an April renegotiation. Paying a
  partner a percentage that silently moved under an already-issued statement
  is the failure this shape exists to prevent.

Both carry a partial unique index guarding a "at most one open row" invariant
— one pending application per account, one live commission per account. Those
are the invariants that turn into a double payout or a stale approval if the
application layer is the only thing enforcing them.
"""

import sqlalchemy as sa
from alembic import op

revision = "d4b8c1e05a37"
down_revision = "c3f7a1d20b64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "partner_applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("expected_minutes_per_month", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("company_website", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("submitted_by", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_partner_applications_id", "partner_applications", ["id"], unique=False
    )
    op.create_index(
        "ix_partner_applications_status",
        "partner_applications",
        ["status"],
        unique=False,
    )
    # One open application per account: a customer who clicks submit twice
    # otherwise puts two rows in the queue, and a reviewer approves the one
    # that is already stale.
    op.create_index(
        "uq_partner_applications_pending",
        "partner_applications",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "partner_commissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("commission_bps", sa.Integer(), nullable=False),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("set_by", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["partner_applications.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["set_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        # A negative commission is a charge and 100% of total spend is the
        # whole invoice. Neither is something anybody means to type, and both
        # are cheap to refuse here rather than discover on a payout run.
        sa.CheckConstraint(
            "commission_bps >= 0 AND commission_bps <= 10000",
            name="ck_partner_commissions_bps_range",
        ),
    )
    op.create_index(
        "ix_partner_commissions_id", "partner_commissions", ["id"], unique=False
    )
    op.create_index(
        "ix_partner_commissions_org_effective",
        "partner_commissions",
        ["organization_id", "effective_from"],
        unique=False,
    )
    op.create_index(
        "uq_partner_commissions_open",
        "partner_commissions",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_partner_commissions_open", table_name="partner_commissions")
    op.drop_index(
        "ix_partner_commissions_org_effective", table_name="partner_commissions"
    )
    op.drop_index("ix_partner_commissions_id", table_name="partner_commissions")
    op.drop_table("partner_commissions")
    op.drop_index("uq_partner_applications_pending", table_name="partner_applications")
    op.drop_index("ix_partner_applications_status", table_name="partner_applications")
    op.drop_index("ix_partner_applications_id", table_name="partner_applications")
    op.drop_table("partner_applications")
