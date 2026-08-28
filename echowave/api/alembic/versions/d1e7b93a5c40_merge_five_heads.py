"""Merge the five independent migration heads into one.

``alembic upgrade head`` fails outright with more than one head — "Multiple
head revisions are present; please specify a specific target revision" — so
until this exists, the migration step of a deploy cannot run at all. That is
what this fixes, and it is the only thing it does: a merge revision carries
no DDL of its own.

Four of the five heads pre-date the pricing/billing work and reached the
default branch independently, each one the tip of a feature branch that was
merged without resolving heads:

* ``a3c9e1b47d02`` — export plan id (``subscription_plans``)
* ``a7d15e93b2c4`` — per-turn token usage (``call_turn_metrics``)
* ``a91c4d7e5b02`` — referral attribution and statements (``organizations``)
* ``f18a4d3c07e9`` — notifications (new table)
* ``b6d3f0a4c9e5`` — embedding ingestion cost (new table)

Merging is safe here because the five touch disjoint schema: three add
columns to three different existing tables, two create new tables, and no
two of them name the same object. That was checked before writing this
rather than assumed — a merge revision cannot resolve a genuine collision,
it only declares that none exists, so the check is the work and this
paragraph is the record of it.

**Ordering is not implied.** A merge revision says these five branches are
independent, not that they run in a particular sequence. If any pair ever
turns out to depend on the other, the fix is a real dependency in the
branch itself, not here.

Revision ID: d1e7b93a5c40
Revises: a3c9e1b47d02, a7d15e93b2c4, a91c4d7e5b02, b6d3f0a4c9e5, f18a4d3c07e9
"""

revision = "d1e7b93a5c40"
down_revision = (
    "a3c9e1b47d02",
    "a7d15e93b2c4",
    "a91c4d7e5b02",
    "b6d3f0a4c9e5",
    "f18a4d3c07e9",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No DDL. A merge revision exists to join branches, nothing more."""


def downgrade() -> None:
    """No DDL. Downgrading past this splits the history back into five heads."""
