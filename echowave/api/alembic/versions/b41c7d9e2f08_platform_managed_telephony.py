"""platform managed telephony

Marks telephony configurations whose numbers sit under Decibyl's own carrier
account. Only these are gated on our KYC flow — a bring-your-own configuration
uses credentials the customer already verified in their own name with their own
carrier, and blocking it on a verification we run would be wrong.

Defaults to false, so every existing configuration is bring-your-own and no
account that can place calls today stops being able to.

Revision ID: b41c7d9e2f08
Revises: 762afdada0cd
Create Date: 2026-07-30 09:14:02.117841

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b41c7d9e2f08'
down_revision: Union[str, None] = '762afdada0cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telephony_configurations",
        sa.Column(
            "is_platform_managed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("telephony_configurations", "is_platform_managed")
