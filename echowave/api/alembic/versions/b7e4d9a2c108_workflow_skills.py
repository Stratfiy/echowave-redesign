"""Skills an account installed, and the bots they are on.

The catalogue itself is files on disk (services/skills/catalogue). This is
what an account did with it, and it is one table rather than two: a row with
no ``workflow_id`` is "installed, not on a bot yet", which is the state the
shelf calls Installed. A row with one is the skill on that bot.

Revision ID: b7e4d9a2c108
Revises: a97c10ebd018
"""

import sqlalchemy as sa
from alembic import op

revision = "b7e4d9a2c108"
down_revision = "a97c10ebd018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organisation_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # The catalogue slug. Not a foreign key: the catalogue is a file on
        # disk, and a skill withdrawn from a release should leave the row
        # rather than fail the migration that removed the file.
        sa.Column("slug", sa.String(length=64), nullable=False),
        # NULL is "installed, not on a bot".
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("added_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # One row per skill per bot, and one "installed" row per skill. Postgres
    # treats NULLs as distinct in a unique index, so the installed row needs
    # its own partial index or a second install would duplicate it.
    op.create_index(
        "uq_organisation_skills_bot",
        "organisation_skills",
        ["organization_id", "slug", "workflow_id"],
        unique=True,
        postgresql_where=sa.text("workflow_id IS NOT NULL"),
    )
    op.create_index(
        "uq_organisation_skills_installed",
        "organisation_skills",
        ["organization_id", "slug"],
        unique=True,
        postgresql_where=sa.text("workflow_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_organisation_skills_installed", table_name="organisation_skills")
    op.drop_index("uq_organisation_skills_bot", table_name="organisation_skills")
    op.drop_table("organisation_skills")
