"""Referral attribution, and the statements a commission is paid against

The partner programme shipped with a rate and nothing for it to be a
percentage of: no way to say which customers a partner introduced, nothing
that computed what they had earned, and no record of having paid them. This is
those three.

Attribution lives on ``organizations`` rather than in a join table because it
is one nullable pointer per account, written once at provisioning and never
again — a table would imply a history that deliberately does not exist.

Every column and table here is additive. Nothing existing changes shape, and
an account with no referral reads exactly as it did before.
"""

import sqlalchemy as sa
from alembic import op

revision = "a91c4d7e5b02"
down_revision = "e5c9d2f1a648"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations", sa.Column("referral_code", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "organizations",
        sa.Column("referred_by_organization_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("referred_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Unique rather than merely indexed: the code is the lookup key on a signup
    # and two accounts sharing one would attribute a customer to whichever row
    # the planner reached first.
    op.create_index(
        "ix_organizations_referral_code",
        "organizations",
        ["referral_code"],
        unique=True,
    )
    op.create_index(
        "ix_organizations_referred_by",
        "organizations",
        ["referred_by_organization_id"],
    )
    # SET NULL: deleting a partner must not delete the customers they
    # introduced, who are ordinary accounts with their own balance and calls.
    op.create_foreign_key(
        "fk_organizations_referred_by",
        "organizations",
        "organizations",
        ["referred_by_organization_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "partner_statements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("partner_organization_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column(
            "amount_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "basis_amount_paise",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_reference", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["partner_organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # One statement per partner per period. Without it a second generate
        # run doubles what we owe — the one arithmetic error nobody notices
        # until after it has been paid.
        sa.UniqueConstraint(
            "partner_organization_id",
            "period_start",
            name="uq_partner_statements_period",
        ),
    )
    op.create_index(
        "ix_partner_statements_id", "partner_statements", ["id"], unique=False
    )
    op.create_index(
        "ix_partner_statements_status", "partner_statements", ["status"], unique=False
    )

    op.create_table(
        "partner_statement_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("statement_id", sa.Integer(), nullable=False),
        sa.Column("referred_organization_id", sa.Integer(), nullable=True),
        sa.Column("referred_name", sa.String(), nullable=True),
        sa.Column(
            "basis_amount_paise",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "amount_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["partner_statements.id"], ondelete="CASCADE"
        ),
        # SET NULL, not CASCADE: a referred account closing must not erase the
        # line that says we owe somebody for the months it was open.
        sa.ForeignKeyConstraint(
            ["referred_organization_id"], ["organizations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_partner_statement_lines_id",
        "partner_statement_lines",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_partner_statement_lines_statement",
        "partner_statement_lines",
        ["statement_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_partner_statement_lines_statement", table_name="partner_statement_lines"
    )
    op.drop_index("ix_partner_statement_lines_id", table_name="partner_statement_lines")
    op.drop_table("partner_statement_lines")
    op.drop_index("ix_partner_statements_status", table_name="partner_statements")
    op.drop_index("ix_partner_statements_id", table_name="partner_statements")
    op.drop_table("partner_statements")
    op.drop_constraint(
        "fk_organizations_referred_by", "organizations", type_="foreignkey"
    )
    op.drop_index("ix_organizations_referred_by", table_name="organizations")
    op.drop_index("ix_organizations_referral_code", table_name="organizations")
    op.drop_column("organizations", "referred_at")
    op.drop_column("organizations", "referred_by_organization_id")
    op.drop_column("organizations", "referral_code")
