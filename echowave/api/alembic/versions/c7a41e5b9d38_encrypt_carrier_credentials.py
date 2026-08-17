"""Encrypt carrier credentials already stored in plaintext.

`telephony_configurations.credentials` is plain JSONB and held auth tokens in
cleartext, while every other secret on the platform is Fernet-encrypted. New
and updated rows are encrypted by the DB client from now on; this rewrites the
rows that are already there.

The account-id field (Plivo `auth_id`, Twilio `account_sid`) is deliberately
left readable — inbound dispatch matches it in SQL, so encrypting it would
break routing for every tenant. See services/telephony/credential_encryption.

**No PLATFORM_CREDENTIAL_SECRET, no rewrite.** The migration logs and moves on
rather than failing the deploy: the application already treats an unset secret
as "store plaintext, as before", so refusing here would take down a deployment
to protect data it is not yet configured to protect. Set the secret and re-run.

Idempotent — an already-encrypted value carries a marker and is skipped — so
running it twice, or after some rows have been re-saved through the app, is
safe.

Revision ID: c7a41e5b9d38
Revises: b3d7f1a95c24
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7a41e5b9d38"
down_revision: Union[str, None] = "b3d7f1a95c24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from loguru import logger

    from api.services.telephony import credential_encryption

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, provider, credentials FROM telephony_configurations")
    ).fetchall()

    if not rows:
        return

    encrypted = 0
    for row in rows:
        current = row.credentials or {}
        if not isinstance(current, dict):
            continue
        updated = credential_encryption.encrypt(row.provider, current)
        if updated == current:
            continue
        bind.execute(
            sa.text(
                "UPDATE telephony_configurations "
                "SET credentials = CAST(:creds AS jsonb) WHERE id = :id"
            ),
            {"creds": json.dumps(updated), "id": row.id},
        )
        encrypted += 1

    logger.info(
        "Encrypted carrier credentials on {} of {} telephony configurations.",
        encrypted,
        len(rows),
    )


def downgrade() -> None:
    """Decrypt back to plaintext, so the previous release can read them."""
    from api.services.telephony import credential_encryption

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, provider, credentials FROM telephony_configurations")
    ).fetchall()

    for row in rows:
        current = row.credentials or {}
        if not isinstance(current, dict):
            continue
        updated = credential_encryption.decrypt(row.provider, current)
        if updated == current:
            continue
        bind.execute(
            sa.text(
                "UPDATE telephony_configurations "
                "SET credentials = CAST(:creds AS jsonb) WHERE id = :id"
            ),
            {"creds": json.dumps(updated), "id": row.id},
        )
