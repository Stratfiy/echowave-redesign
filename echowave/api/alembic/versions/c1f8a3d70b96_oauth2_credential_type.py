"""Add the oauth2 value to the credential type enum.

The refresh-token grant that lets a tool keep working past the hour a vendor's
access token lasts. See api/services/integrations/oauth2.py for why the stored
credential is the refresh token and the access token beside it is a cache.

Postgres cannot drop a value from an enum, so the downgrade rewrites the type
without it — and refuses while any row still uses it rather than silently
losing a customer's connected account.

Revision ID: c1f8a3d70b96
Revises: e9b4c72a1f36
"""

from alembic import op

revision = "c1f8a3d70b96"
down_revision = "e9b4c72a1f36"
branch_labels = None
depends_on = None

ENUM_NAME = "webhook_credential_type"
NEW_VALUE = "oauth2"
EXISTING = ("none", "api_key", "bearer_token", "basic_auth", "custom_header")


def upgrade() -> None:
    # IF NOT EXISTS so a re-run is a no-op; ALTER TYPE ... ADD VALUE cannot run
    # inside a transaction block on older servers, and alembic wraps each
    # migration in one, so this is the autocommit escape.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE {ENUM_NAME} ADD VALUE IF NOT EXISTS '{NEW_VALUE}'")


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM external_credentials
                WHERE credential_type = '{NEW_VALUE}'
            ) THEN
                RAISE EXCEPTION
                    'Credentials still use {NEW_VALUE}; migrate or delete them first';
            END IF;
        END $$;
        """
    )
    values = ", ".join(f"'{v}'" for v in EXISTING)
    op.execute(f"ALTER TYPE {ENUM_NAME} RENAME TO {ENUM_NAME}_old")
    op.execute(f"CREATE TYPE {ENUM_NAME} AS ENUM ({values})")
    op.execute(
        f"ALTER TABLE external_credentials "
        f"ALTER COLUMN credential_type TYPE {ENUM_NAME} "
        f"USING credential_type::text::{ENUM_NAME}"
    )
    op.execute(f"DROP TYPE {ENUM_NAME}_old")
