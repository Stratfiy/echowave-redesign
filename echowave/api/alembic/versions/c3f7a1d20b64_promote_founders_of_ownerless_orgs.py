"""Give every ownerless organization its founder back

``provision_new_account`` created the organization for a brand-new signup and
then added that person to it with the default role — MEMBER. So the only human
in a company they had just founded could not reach a single ADMIN-gated
surface: provider keys, the billing profile, the autopay mandate, removing a
number from the do-not-call list, minting a tool credential. Nor could they fix
it, because promoting a member is OWNER-gated. The account was a dead end from
the moment it was created.

The code path is fixed. This is for the accounts created while it was not.

Who to promote: the earliest membership in any organization that has no Owner
at all. That row is the founder by construction — an organization's provider id
is derived from its first user's, and every later member is added by someone
already inside. An organization cannot lose its last Owner (the API refuses the
demotion and the removal), so "no Owner" only ever means "never had one".

Deliberately not reversible: see ``downgrade``.
"""

from alembic import op

revision = "c3f7a1d20b64"
down_revision = "b7c41e9a2f30"
branch_labels = None
depends_on = None


#: Exposed so a test can run the real statement rather than a paraphrase of it.
#: A backfill that is only ever executed by `alembic upgrade` is a backfill
#: nobody has watched pick the right row.
PROMOTE_FOUNDERS_SQL = """
    WITH ownerless AS (
        SELECT organization_id
        FROM organization_memberships
        GROUP BY organization_id
        HAVING count(*) FILTER (WHERE role = 'owner') = 0
    ),
    founder AS (
        SELECT DISTINCT ON (m.organization_id) m.id
        FROM organization_memberships m
        JOIN ownerless o ON o.organization_id = m.organization_id
        ORDER BY m.organization_id, m.created_at NULLS LAST, m.id
    )
    UPDATE organization_memberships
    SET role = 'owner'
    WHERE id IN (SELECT id FROM founder)
"""


def upgrade() -> None:
    op.execute(PROMOTE_FOUNDERS_SQL)


def downgrade() -> None:
    """Intentionally a no-op.

    The rows this promoted are indistinguishable afterwards from an Owner
    somebody granted on purpose, so a truthful downgrade is not available. The
    honest choice between leaving an extra Owner and demoting a real one is the
    first: demoting would restore the lockout this migration exists to end, on
    accounts that have since been used normally.
    """
