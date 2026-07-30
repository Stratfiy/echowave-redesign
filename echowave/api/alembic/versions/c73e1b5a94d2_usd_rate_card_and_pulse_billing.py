"""usd rate card and pulse billing

Two changes to how the platform fee is priced.

**The list price moves to USD.** Voice-AI platforms compete on a dollar figure,
so ours is fixed at $0.02/min and converted to rupees at the rate in force when
the call happened. usd_inr_rate_history holds that rate, effective-dated like
every other rate here, so an old invoice recomputes to the number that was
actually charged rather than to whatever the rupee is worth today. Rate rows
now carry either a dollar price or a rupee one — never both, enforced by a
check constraint — because a contract written in rupees must not be exposed to
FX, and two populated columns would leave two answers to "what does this
account pay".

**Time is billed in 15-second pulses, not whole minutes.** This is the
commercial differentiator. Existing rows are backfilled with pulse_seconds = 60
so already-costed calls keep the terms they were billed under; only the global
default is 15. Calls costed before this migration are untouched — costing is
idempotent and their receipts stand.

Existing INR rate overrides are left as INR overrides. They were agreed in
rupees, and silently converting them to a dollar price would change the deal.

Revision ID: c73e1b5a94d2
Revises: b41c7d9e2f08
Create Date: 2026-07-30 03:41:55.208310

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c73e1b5a94d2"
down_revision: Union[str, None] = "b41c7d9e2f08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ₹96.00 per dollar, the rate at the time this landed. Seeded as the opening
# row so the fallback constant in money.py is never the thing billing runs on.
SEED_PAISE_PER_USD = 9_600


def upgrade() -> None:
    op.create_table(
        "usd_inr_rate_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("paise_per_usd", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("set_by", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("paise_per_usd > 0", name="ck_usd_inr_rate_positive"),
        sa.ForeignKeyConstraint(["set_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_usd_inr_rate_history_id"), "usd_inr_rate_history", ["id"])
    op.create_index(
        "ix_usd_inr_rate_history_effective",
        "usd_inr_rate_history",
        ["effective_from", "effective_to"],
    )
    op.create_index(
        "uq_usd_inr_rate_history_open",
        "usd_inr_rate_history",
        ["effective_to"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )

    # Opening rate, effective from the epoch so it covers every call already in
    # the system. Without this, recosting an old call would hit the fallback.
    op.execute(
        sa.text(
            "INSERT INTO usd_inr_rate_history "
            "(paise_per_usd, effective_from, effective_to, source, note, created_at) "
            "VALUES (:rate, '1970-01-01T00:00:00+00:00', NULL, 'migration', "
            "'Opening rate at the move to USD pricing', NOW())"
        ).bindparams(rate=SEED_PAISE_PER_USD)
    )

    # --- account overrides: either currency, plus a pulse ---
    op.add_column(
        "organization_rate_history",
        sa.Column("platform_rate_micros_usd", sa.Integer(), nullable=True),
    )
    op.add_column(
        "organization_rate_history",
        sa.Column("pulse_seconds", sa.Integer(), nullable=True),
    )
    # Existing overrides were agreed in rupees at whole-minute billing. Pin both
    # so nobody's terms change under them.
    op.execute(
        "UPDATE organization_rate_history SET pulse_seconds = 60 "
        "WHERE pulse_seconds IS NULL"
    )
    op.alter_column("organization_rate_history", "platform_rate_mpaise", nullable=True)
    op.create_check_constraint(
        "ck_org_rate_history_one_currency",
        "organization_rate_history",
        "(platform_rate_micros_usd IS NOT NULL)::int "
        "+ (platform_rate_mpaise IS NOT NULL)::int = 1",
    )

    # --- volume tiers: same treatment ---
    op.add_column(
        "platform_volume_tiers",
        sa.Column("platform_rate_micros_usd", sa.Integer(), nullable=True),
    )
    op.add_column(
        "platform_volume_tiers",
        sa.Column("pulse_seconds", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE platform_volume_tiers SET pulse_seconds = 60 "
        "WHERE pulse_seconds IS NULL"
    )
    op.alter_column("platform_volume_tiers", "platform_rate_mpaise", nullable=True)
    op.create_check_constraint(
        "ck_platform_volume_tiers_one_currency",
        "platform_volume_tiers",
        "(platform_rate_micros_usd IS NOT NULL)::int "
        "+ (platform_rate_mpaise IS NOT NULL)::int = 1",
    )

    # --- receipt snapshot: how the rupee figure was arrived at ---
    op.add_column(
        "workflow_runs",
        sa.Column("platform_rate_micros_usd_applied", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("usd_inr_paise_applied", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("pulse_seconds_applied", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workflow_runs", sa.Column("billed_seconds", sa.Integer(), nullable=True)
    )
    # Already-costed calls were billed by the whole minute. Record that, rather
    # than leaving a null that later reads as "billed at today's pulse".
    op.execute(
        "UPDATE workflow_runs SET pulse_seconds_applied = 60, "
        "billed_seconds = CEIL(billable_seconds::numeric / 60) * 60 "
        "WHERE costed_at IS NOT NULL AND billable_seconds IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "billed_seconds")
    op.drop_column("workflow_runs", "pulse_seconds_applied")
    op.drop_column("workflow_runs", "usd_inr_paise_applied")
    op.drop_column("workflow_runs", "platform_rate_micros_usd_applied")

    op.drop_constraint(
        "ck_platform_volume_tiers_one_currency",
        "platform_volume_tiers",
        type_="check",
    )
    # Anything priced in dollars has no rupee figure to fall back to, so it
    # cannot survive the downgrade. Converting at today's rate would invent a
    # price nobody agreed to; dropping the rows is the honest failure.
    op.execute("DELETE FROM platform_volume_tiers WHERE platform_rate_mpaise IS NULL")
    op.alter_column("platform_volume_tiers", "platform_rate_mpaise", nullable=False)
    op.drop_column("platform_volume_tiers", "pulse_seconds")
    op.drop_column("platform_volume_tiers", "platform_rate_micros_usd")

    op.drop_constraint(
        "ck_org_rate_history_one_currency",
        "organization_rate_history",
        type_="check",
    )
    op.execute(
        "DELETE FROM organization_rate_history WHERE platform_rate_mpaise IS NULL"
    )
    op.alter_column("organization_rate_history", "platform_rate_mpaise", nullable=False)
    op.drop_column("organization_rate_history", "pulse_seconds")
    op.drop_column("organization_rate_history", "platform_rate_micros_usd")

    op.drop_index("uq_usd_inr_rate_history_open", table_name="usd_inr_rate_history")
    op.drop_index(
        "ix_usd_inr_rate_history_effective", table_name="usd_inr_rate_history"
    )
    op.drop_index(op.f("ix_usd_inr_rate_history_id"), table_name="usd_inr_rate_history")
    op.drop_table("usd_inr_rate_history")
