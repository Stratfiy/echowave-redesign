"""A bot's address, separate from its name

``workflows.handle``.

Mentions shipped resolving ``@`` against a handle *derived from the display
name*, which meant the only way to give a bot a stable address was to rename
it — and the rename this produced was 'Narayani Dental front desk' becoming
'narayani-dental-front-desk'. That is a config key, not a name. It is read by
whoever runs the clinic, every morning, in a sidebar.

So the two jobs separate. The display name is written by a person and says what
the bot is; the handle is typed by a person and has to be unique, short and
stable. A column rather than a convention, because the moment it is derived it
is not stable: renaming the bot would silently change its address and every
message that referenced it would be addressing nothing.

**Collisions get a number here, where the standalone script refused them.**
That difference is deliberate. The script's refusal was right when the handle
*was* the name: picking a winner would have left a business with a bot whose
name quietly changed. Nothing is lost that way now. Both bots keep the name
they were given, and the second one is addressed as ``@customer-support-2``
rather than being left with no address at all — which is the outcome a refusal
produces in a migration nobody is watching.

The unique index is partial. ``handle IS NULL`` is a bot with no address yet,
which is legal and must stay legal: a NOT NULL column here would mean an insert
on any path that has not been taught about handles fails outright, and the path
that matters is the one that creates a customer's first agent.

The slug logic is inlined rather than imported from
``api.services.workflow.mentions``. A migration is a record of what was run
against a database on a particular day; importing application code makes it a
record of whatever that code says today.
"""

import re
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "f2c6a83e70d1"
down_revision = "d5f1b8c30a74"
branch_labels = None
depends_on = None

_UNWANTED = re.compile(r"[^a-z0-9\s_-]+")
_SEPARATORS = re.compile(r"[\s_-]+")

#: Room for a suffix inside the column. The handle is typed, so the limit is
#: about what somebody will type rather than about storage.
MAX_HANDLE = 64


def _slug(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = _UNWANTED.sub(" ", folded.casefold())
    return _SEPARATORS.sub("-", folded).strip("-")[:MAX_HANDLE].strip("-")


def upgrade() -> None:
    op.add_column(
        "workflows", sa.Column("handle", sa.String(MAX_HANDLE), nullable=True)
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, organization_id, name FROM workflows ORDER BY organization_id, id"
        )
    ).fetchall()

    # Per organisation, because that is the scope the index constrains. Two
    # accounts may both have a @front-desk and neither is anybody else's
    # business.
    taken: dict[object, set[str]] = {}
    for workflow_id, organization_id, name in rows:
        base = _slug(name or "")
        if not base:
            # A name with nothing sluggable in it — every character punctuation,
            # or a script this folding does not reduce. Left NULL rather than
            # given "workflow-41": an address nobody can guess is no better
            # than no address, and NULL is at least honest about it.
            continue
        used = taken.setdefault(organization_id, set())
        handle = base
        suffix = 2
        while handle in used:
            tail = f"-{suffix}"
            handle = f"{base[: MAX_HANDLE - len(tail)]}{tail}"
            suffix += 1
        used.add(handle)
        connection.execute(
            sa.text("UPDATE workflows SET handle = :handle WHERE id = :id"),
            {"handle": handle, "id": workflow_id},
        )

    op.create_index(
        "ix_workflows_organization_handle",
        "workflows",
        ["organization_id", "handle"],
        unique=True,
        postgresql_where=sa.text("handle IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_workflows_organization_handle", table_name="workflows")
    op.drop_column("workflows", "handle")
