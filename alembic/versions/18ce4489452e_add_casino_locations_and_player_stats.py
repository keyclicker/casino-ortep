"""add casino_locations and player stats

Revision ID: 18ce4489452e
Revises: 2a2bb8d3aa6b
Create Date: 2026-03-27

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '18ce4489452e'
down_revision: Union[str, Sequence[str], None] = '2a2bb8d3aa6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add casino_locations table, drop config, add total_won/total_lost to players."""
    op.create_table(
        'casino_locations',
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('group_id'),
    )
    op.drop_table('config')
    op.add_column('players', sa.Column('total_won', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('players', sa.Column('total_lost', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    """Reverse: drop casino_locations, restore config, remove stat columns."""
    op.drop_column('players', 'total_lost')
    op.drop_column('players', 'total_won')
    op.create_table(
        'config',
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.drop_table('casino_locations')
