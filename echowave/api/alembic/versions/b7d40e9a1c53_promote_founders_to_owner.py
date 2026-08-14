"""promote every organization's founder to owner

``provision_new_account`` added the founder with ``add_user_to_organization``'s
default role, which is MEMBER. So everybody who signed up became a member of
the organization they had just created — unable to change a role, remove
anybody, or invite anybody, on an account nobody else was in.

The role machinery was enforced correctly the whole time. The founder simply
never got the role, which made it look like the roles did nothing.

Fixing the code fixes new accounts. Every existing one is still stuck, and this
promotes them: for each organization with **no owner at all**, the earliest
membership row is the founder, and it becomes OWNER.

Deliberately narrow. An organization that already has an owner is left exactly
as it is, so this cannot promote anybody on an account where the question has
already been decided — including one where an owner deliberately demoted
themselves after adding somebody else.

Irreversible by design: the downgrade is a no-op. Demoting owners on the way
down would take away access somebody has been using, and there is no record of
which of them this migration created.

Revision ID: b7d40e9a1c53
Revises: a3c81d05e6f2
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b7d40e9a1c53"
down_revision: Union[str, Sequence[str], None] = "a3c81d05e6f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # DISTINCT ON picks one row per organization — the earliest membership,
    # which is the founder. Restricted to organizations holding no owner, so an
    # account where ownership is already settled is untouched.
    op.execute(
        """
        UPDATE organization_memberships AS m
        SET role = 'owner'
        FROM (
            SELECT DISTINCT ON (organization_id) id
            FROM organization_memberships
            WHERE organization_id NOT IN (
                SELECT organization_id
                FROM organization_memberships
                WHERE role = 'owner'
            )
            ORDER BY organization_id, id
        ) AS founder
        WHERE m.id = founder.id
        """
    )


def downgrade() -> None:
    """No-op, on purpose.

    Demoting owners would remove access people are relying on, and nothing
    records which owners this migration created versus which were always
    there.
    """
