"""referrals: attribution on the organization, and awards held before they land

A referrer earns 20% of the first payment made by the account they referred.
The award is **earned at settlement and spendable after a hold**, which is why
it needs a table of its own rather than a credit ledger row with a date on it.

The ledger means one thing — money that is yours now — and every balance query
in the system depends on that. An award sitting there with an availability date
would make each of those queries wrong by exactly the amount nobody could
spend.

The hold exists because card disputes arrive weeks after the payment. Credited
at settlement and never reversed, a referral scheme is a way to buy credit at a
discount with a stolen card. Cancelling an award before it matures writes
nothing at all, so a clawback leaves no correction to explain.

``referred_organization_id`` is unique: the reward is for the referral, not for
each payment that account goes on to make. Enforced here rather than by a check
in the service, because two concurrent webhooks for the same order race that
check and both win.

Revision ID: c4e93f0b28a7
Revises: b7d40e9a1c53
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e93f0b28a7"
down_revision: Union[str, Sequence[str], None] = "b7d40e9a1c53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    op.create_index(
        op.f("ix_organizations_referral_code"),
        "organizations",
        ["referral_code"],
        unique=True,
    )
    op.create_foreign_key(
        "fk_organizations_referred_by",
        "organizations",
        "organizations",
        ["referred_by_organization_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "referral_awards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("referrer_organization_id", sa.Integer(), nullable=False),
        sa.Column("referred_organization_id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.String(length=64), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["referrer_organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["referred_organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # One award per referred account, at the database rather than in code.
        sa.UniqueConstraint("referred_organization_id", name="uq_referral_per_account"),
    )
    op.create_index(op.f("ix_referral_awards_id"), "referral_awards", ["id"])
    op.create_index(
        op.f("ix_referral_awards_referrer"),
        "referral_awards",
        ["referrer_organization_id"],
    )
    op.create_index(
        op.f("ix_referral_awards_payment"), "referral_awards", ["payment_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_referral_awards_payment"), table_name="referral_awards")
    op.drop_index(op.f("ix_referral_awards_referrer"), table_name="referral_awards")
    op.drop_index(op.f("ix_referral_awards_id"), table_name="referral_awards")
    op.drop_table("referral_awards")

    op.drop_constraint(
        "fk_organizations_referred_by", "organizations", type_="foreignkey"
    )
    op.drop_index(op.f("ix_organizations_referral_code"), table_name="organizations")
    op.drop_column("organizations", "referred_at")
    op.drop_column("organizations", "referred_by_organization_id")
    op.drop_column("organizations", "referral_code")
