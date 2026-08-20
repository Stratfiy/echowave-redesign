"""starter plan: one balance grant per collected cycle

The guard behind the plan's balance half. Razorpay delivers webhooks at least
once, not exactly once, so a redelivered ``subscription.charged`` would credit a
second Rs2,500 to an account that paid for one — real money, and the kind of
error that reconciles perfectly against itself because both ledger rows look
correct in isolation.

The ref is the provider's **payment id**, which is identical on every
redelivery of the same collection. Keying off the period instead would not
work: recording a collection advances the charge to the next period, so a
redelivery computes a different period and misses the constraint entirely.

Autogenerate also proposed a dozen unrelated changes — server defaults it reads
differently from the model, and two partial indexes whose ``postgresql_where``
it renders in a form it then fails to recognise on the next run. None of them
are this change and all of them are pre-existing drift, so they were removed
by hand rather than carried along where a review would have to separate them
from the index that matters.

Revision ID: 548ce08a26ac
Revises: d5c93b7e2a41
Create Date: 2026-08-20 13:17:45.996244

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "548ce08a26ac"
down_revision: Union[str, None] = "d5c93b7e2a41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_credit_ledger_plan_ref"
_WHERE = sa.text("kind = 'plan' AND ref_id IS NOT NULL")


def upgrade() -> None:
    op.create_index(
        _INDEX,
        "credit_ledger",
        ["organization_id", "ref_type", "ref_id"],
        unique=True,
        postgresql_where=_WHERE,
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="credit_ledger", postgresql_where=_WHERE)
