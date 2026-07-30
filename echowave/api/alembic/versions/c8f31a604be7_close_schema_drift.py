"""close the schema drift between models and database

`alembic check` has been failing since before this work, and the consequence was
worse than a failing check: every `--autogenerate` run proposed **dropping
workflow_definitions.call_disposition_codes**, a NOT NULL column holding data on
every published version of every workflow. Anyone generating a migration had to
know to delete that line by hand.

Two directions of drift, resolved deliberately rather than by whatever
autogenerate happened to suggest:

* `idx_queued_runs_campaign_state_optimized` was declared on the model but never
  created. It is a partial index on the campaign dispatcher's hot query
  (`WHERE state = 'queued'`), so creating it is what the model always intended.
* `call_disposition_codes` existed in the database but not on the model. Added
  to the model in this change; the column itself already exists, so there is
  nothing to do here for it.

The server defaults were mine, from the payments, reservation and GST
migrations: the migrations set `server_default` while the models declared only a
Python-side `default`. Now declared on both sides. No DDL either — the database
is already correct.

Revision ID: c8f31a604be7
Revises: b7e42d9c05af
Create Date: 2026-07-30 11:04:18.220417

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8f31a604be7"
down_revision: Union[str, None] = "b7e42d9c05af"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS because a database that was created from the models rather
    # than by replaying migrations may already have it.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_queued_runs_campaign_state_optimized "
        "ON queued_runs (campaign_id, state) WHERE state = 'queued'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_queued_runs_campaign_state_optimized")
