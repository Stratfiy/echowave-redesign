"""One credit per captured payment.

The Razorpay webhook is delivered at least once. Its idempotency check was a
plain read-then-write, and the ledger credit was written before any row the two
deliveries had in common — so two overlapping deliveries of one `payment.captured`
both read `status='created'` and both credited the account.

The handler now takes a row lock on the payment, which makes the ordinary retry
a no-op. This index is the backstop for the case a lock in one process cannot
cover, and it matches the shape already used for usage, reservation and rental
refs.

Created CONCURRENTLY: `credit_ledger` is the hottest table in the billing path
and an ACCESS EXCLUSIVE lock during a deploy would stall every call that reserves
or debits credit.

Revision ID: b3d7f1a95c24
Revises: a91c4d7e5b02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3d7f1a95c24"
down_revision: Union[str, None] = "a91c4d7e5b02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "uq_credit_ledger_topup_ref"


def upgrade() -> None:
    # If the bug this index prevents has already fired, CREATE UNIQUE INDEX
    # CONCURRENTLY fails *and leaves an INVALID index behind* that silently does
    # nothing. Look first, and stop with the affected payments named — deciding
    # what to do about money already credited twice is a human's call, not a
    # migration's.
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT organization_id, ref_id, COUNT(*) AS n
                FROM credit_ledger
                WHERE kind = 'topup' AND ref_id IS NOT NULL
                GROUP BY organization_id, ref_type, ref_id
                HAVING COUNT(*) > 1
                ORDER BY n DESC
                LIMIT 20
                """
            )
        )
        .fetchall()
    )
    if duplicates:
        listed = ", ".join(
            f"org {row.organization_id} payment {row.ref_id} x{row.n}"
            for row in duplicates
        )
        raise RuntimeError(
            "credit_ledger already holds duplicate top-up credits, so the "
            "uniqueness this migration adds cannot be established. These "
            "accounts were credited more than once for a single payment and "
            "need a staff adjustment before the index can be created: "
            f"{listed}. Reverse the surplus credits, then re-run the migration."
        )

    # CONCURRENTLY cannot run inside a transaction block, and Alembic wraps
    # migrations in one by default.
    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
            ON credit_ledger (organization_id, ref_type, ref_id)
            WHERE kind = 'topup' AND ref_id IS NOT NULL
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
