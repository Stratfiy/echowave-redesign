"""Rename the columns the rebrand renamed inside an already-applied migration.

Revision ID: f2b9d47c30ae
Revises: e5c81b73f9a2

The rebrand changed `quota_dograh_tokens` to `quota_decibyl_tokens` (and the two
`used_*`/`quota_*` columns on the usage-cycle table) **by editing the migration
that created them** rather than by adding one that renames them.

On a database created after that edit, everything is correct — which is why
every test, every fresh install and every CI run passes. On a database created
*before* it, alembic is long past that revision, nothing ever renames anything,
and the column keeps its old name forever. The models then ask for a column that
does not exist, and every query touching `organizations` fails with
`UndefinedColumnError` — the admin dashboard, the billing pages, the account
list. Found exactly that way: on a deployment upgraded from the pre-rebrand
codebase, where the whole billing section returned 500 while a fresh install of
the same commit was fine.

So this migration exists to make the two histories converge. It is written to be
a no-op on a database that already has the new names, because the fresh case is
the common one and must not be disturbed:

* rename only when the old column is present **and** the new one is not
* drop the stale one when both somehow exist (a half-applied rename)
* do nothing when only the new one is there

`downgrade` deliberately does not put the old names back. The old name is a
brand that no longer exists; reintroducing it would only recreate this problem
pointing the other way.
"""

import sqlalchemy as sa
from alembic import op

revision = "f2b9d47c30ae"
down_revision = "e5c81b73f9a2"
branch_labels = None
depends_on = None

#: (table, old column, new column)
RENAMES = (
    ("organizations", "quota_dograh_tokens", "quota_decibyl_tokens"),
    ("organization_usage_cycles", "quota_dograh_tokens", "quota_decibyl_tokens"),
    ("organization_usage_cycles", "used_dograh_tokens", "used_decibyl_tokens"),
)


def _columns(table: str) -> set[str]:
    """What the database actually has, rather than what the models expect.

    Through the inspector rather than a hand-written information_schema query:
    the paramstyle of a raw `exec_driver_sql` is the driver's, and asyncpg does
    not take `%(name)s`, so the literal query fails at exactly the moment this
    migration is most needed.
    """
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    for table, old, new in RENAMES:
        present = _columns(table)
        if not present:
            # Table absent entirely — a partial install, not our problem to fix.
            continue
        if old in present and new not in present:
            op.alter_column(table, old, new_column_name=new)
        elif old in present and new in present:
            # Both names present means a rename was started and not finished.
            # The new column is the one the application reads, so the old one is
            # dead weight that would confuse the next person to look.
            op.drop_column(table, old)


def downgrade() -> None:
    # Intentionally empty. See the module docstring.
    pass
