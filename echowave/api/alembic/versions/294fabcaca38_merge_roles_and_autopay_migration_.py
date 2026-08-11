"""Join the two migration branches that grew in parallel.

Two lines of work landed at the same time and each added migrations on top of
the same parent, which left the tree with **two heads** — and `alembic upgrade
head` refuses to run at all in that state rather than picking one:

* `ae5bb6b2a0f6` — organization roles and staff tiers
* `f18a4d3c07e9` — provider cost, autopay mandates, notifications

Empty on purpose. A merge revision exists to give the two branches one
descendant so there is a single head again; it has no schema of its own. The
two branches touch disjoint tables (`organization_memberships` and `users` on
one side; `payment_mandates`, `recurring_charges`, `recurring_charge_periods`,
`call_cost_items` and `notifications` on the other), so neither ordering
changes the result and there is nothing to reconcile here.

Revision ID: 294fabcaca38
Revises: ae5bb6b2a0f6, f18a4d3c07e9
"""

from typing import Sequence, Union

revision: str = "294fabcaca38"
down_revision: Union[str, Sequence[str], None] = ("ae5bb6b2a0f6", "f18a4d3c07e9")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Nothing to do — see the module docstring."""


def downgrade() -> None:
    """Nothing to undo — see the module docstring."""
