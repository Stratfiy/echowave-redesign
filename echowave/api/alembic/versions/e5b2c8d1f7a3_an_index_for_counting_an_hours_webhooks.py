"""An index for counting an hour's outbound webhooks per organisation

The cap on outbound event webhooks asks, on every event a webhook wants,
"how many has this account sent in the last hour". Without an index that
is a scan of every delivery ever made, on the hot path of every bot event.

Partial on the runless rows, because those are the only rows the question
is about: a call-flow webhook has a run and is capped by the call.

Revision ID: e5b2c8d1f7a3
Revises: d4a07c1b93e2
"""

import sqlalchemy as sa
from alembic import op

revision = "e5b2c8d1f7a3"
down_revision = "d4a07c1b93e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_webhook_deliveries_org_recent_events",
        "webhook_deliveries",
        ["organization_id", "created_at"],
        postgresql_where=sa.text("workflow_run_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_deliveries_org_recent_events", table_name="webhook_deliveries"
    )
