"""People: a person's own contacts with context (PEOPLE.md)

Additive only. Eight new tables; nothing existing is changed, so downgrading
drops exactly what this adds and the flag off leaves the schema unused.

* ``people`` -- one contact of one person (``owner_user_id``), with the brief.
* ``person_handles`` -- normalised numbers and addresses, for lookups.
* ``person_sources`` -- the provider's id for a synced contact.
* ``person_interactions`` -- calls, messages, mail and meetings, one line each.
* ``people_syncs`` -- each person's sync state and cursor per provider.
* ``person_merges`` -- suggested duplicates, merged only when the owner says.
* ``person_shares`` -- a contact card the owner showed to one colleague.
* ``people_settings`` -- whether agents may read briefs (off until chosen).

Revision ID: 20261009people
Revises: 20261008learnplan
"""

import sqlalchemy as sa
from alembic import op

revision = "20261009people"
down_revision = "20261008learnplan"
branch_labels = None
depends_on = None


def _org() -> sa.Column:
    return sa.Column(
        "organization_id",
        sa.Integer(),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def _user(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.Integer(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def _person() -> sa.Column:
    return sa.Column(
        "person_id",
        sa.Integer(),
        sa.ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
    )


def _json(name: str, default: str) -> sa.Column:
    return sa.Column(
        name, sa.JSON(), nullable=False, server_default=sa.text(f"'{default}'::json")
    )


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(36), nullable=False, unique=True),
        _org(),
        _user("owner_user_id"),
        sa.Column("name", sa.String(200), nullable=False),
        _json("phones", "[]"),
        _json("emails", "[]"),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("relation", sa.String(200), nullable=True),
        _json("sources", "[]"),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column("brief_by", sa.String(16), nullable=True),
        _ts("brief_at"),
        _ts("brief_due_at"),
        _ts("last_interaction_at"),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_people_owner", "people", ["organization_id", "owner_user_id"])
    op.create_index(
        "ix_people_brief_due",
        "people",
        ["brief_due_at"],
        postgresql_where=sa.text("brief_due_at IS NOT NULL"),
    )

    op.create_table(
        "person_handles",
        sa.Column("id", sa.Integer(), primary_key=True),
        _person(),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("value", sa.String(320), nullable=False),
    )
    op.create_index(
        "ix_person_handles_lookup",
        "person_handles",
        ["organization_id", "owner_user_id", "kind", "value"],
    )
    op.create_index("ix_person_handles_person", "person_handles", ["person_id"])

    op.create_table(
        "person_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        _person(),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(300), nullable=False),
        sa.Column("etag", sa.String(300), nullable=True),
        _ts("updated_at"),
    )
    op.create_index(
        "ux_person_sources_external",
        "person_sources",
        ["organization_id", "owner_user_id", "provider", "external_id"],
        unique=True,
    )

    op.create_table(
        "person_interactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        _person(),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("line", sa.String(300), nullable=False),
        sa.Column("ref", sa.String(200), nullable=False),
        _ts("at", nullable=False),
    )
    op.create_index(
        "ux_person_interactions_ref",
        "person_interactions",
        ["organization_id", "owner_user_id", "ref"],
        unique=True,
    )
    op.create_index(
        "ix_person_interactions_person_at", "person_interactions", ["person_id", "at"]
    )

    op.create_table(
        "people_syncs",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        _user("user_id"),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        _ts("last_synced_at"),
        _ts("started_at"),
        _json("counts", "{}"),
        _ts("updated_at"),
    )
    op.create_index(
        "ux_people_syncs_owner",
        "people_syncs",
        ["organization_id", "user_id", "provider"],
        unique=True,
    )

    op.create_table(
        "person_merges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "keep_id",
            sa.Integer(),
            sa.ForeignKey("people.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "other_id",
            sa.Integer(),
            sa.ForeignKey("people.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(8), nullable=False),
        sa.Column("value", sa.String(320), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        _ts("created_at"),
        _ts("decided_at"),
    )
    op.create_index(
        "ux_person_merges_pair",
        "person_merges",
        ["owner_user_id", "keep_id", "other_id"],
        unique=True,
    )
    op.create_index(
        "ix_person_merges_owner",
        "person_merges",
        ["organization_id", "owner_user_id", "status"],
    )

    op.create_table(
        "person_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        _person(),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        _user("shared_with_user_id"),
        _ts("created_at"),
    )
    op.create_index(
        "ux_person_shares_pair",
        "person_shares",
        ["person_id", "shared_with_user_id"],
        unique=True,
    )
    op.create_index(
        "ix_person_shares_with",
        "person_shares",
        ["organization_id", "shared_with_user_id"],
    )

    op.create_table(
        "people_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        _user("user_id"),
        sa.Column(
            "agents_may_read",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        _ts("updated_at"),
    )
    op.create_index(
        "ux_people_settings_owner",
        "people_settings",
        ["organization_id", "user_id"],
        unique=True,
    )


def downgrade() -> None:
    for table in (
        "people_settings",
        "person_shares",
        "person_merges",
        "people_syncs",
        "person_interactions",
        "person_sources",
        "person_handles",
        "people",
    ):
        op.drop_table(table)
