"""model-level provider rates

Provider rates gain a ``model`` dimension. Models from one provider differ by
more than an order of magnitude — gpt-4o against gpt-4o-mini — so a rate keyed
only by provider mispriced every call that was not on the default model.

``model = ''`` is the provider-wide fallback and is part of both indexes, so a
fallback and a model-specific override can be open at the same time. Existing
rows become fallbacks, which is exactly the behaviour they had before.

NOTE: autogenerate also swept in two unrelated pre-existing drifts — an added
``idx_queued_runs_campaign_state_optimized`` and a *destructive* drop of
``workflow_definitions.call_disposition_codes``. Both were removed by hand;
they are identical on main and are tracked in KNOWN_ISSUES.md #10.

Revision ID: 1a3f2592ee5a
Revises: 810aaefd657d
Create Date: 2026-07-29 17:23:54.075169

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '1a3f2592ee5a'
down_revision: Union[str, None] = '810aaefd657d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'call_cost_items',
        sa.Column('model', sa.String(length=128), nullable=True),
    )
    op.add_column(
        'provider_rates',
        sa.Column(
            'model', sa.String(length=128), server_default='', nullable=False
        ),
    )

    op.drop_index(op.f('ix_provider_rates_lookup'), table_name='provider_rates')
    op.create_index(
        'ix_provider_rates_lookup',
        'provider_rates',
        ['provider', 'component', 'model', 'effective_from'],
        unique=False,
    )

    op.drop_index(
        op.f('uq_provider_rates_open'),
        table_name='provider_rates',
        postgresql_where='(effective_to IS NULL)',
    )
    op.create_index(
        'uq_provider_rates_open',
        'provider_rates',
        ['provider', 'component', 'model'],
        unique=True,
        postgresql_where=sa.text('effective_to IS NULL'),
    )


def downgrade() -> None:
    # Model-specific rates cannot survive a key that has no model dimension:
    # collapsing them would leave several rows competing for one open slot and
    # the unique index would reject the lot. Drop them and keep the fallbacks,
    # which is precisely the pre-migration state.
    op.execute("DELETE FROM provider_rates WHERE model <> ''")

    op.drop_index(
        'uq_provider_rates_open',
        table_name='provider_rates',
        postgresql_where=sa.text('effective_to IS NULL'),
    )
    op.create_index(
        op.f('uq_provider_rates_open'),
        'provider_rates',
        ['provider', 'component'],
        unique=True,
        postgresql_where='(effective_to IS NULL)',
    )

    op.drop_index('ix_provider_rates_lookup', table_name='provider_rates')
    op.create_index(
        op.f('ix_provider_rates_lookup'),
        'provider_rates',
        ['provider', 'component', 'effective_from'],
        unique=False,
    )

    op.drop_column('provider_rates', 'model')
    op.drop_column('call_cost_items', 'model')
