"""add spin_history table

Revision ID: d4e8b1f0a2c7
Revises: c3f7a9e12d45
Create Date: 2026-06-20

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd4e8b1f0a2c7'
down_revision: Union[str, Sequence[str], None] = 'c3f7a9e12d45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'spin_history',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('cost', sa.Integer(), nullable=False),
        sa.Column('net', sa.Integer(), nullable=False),
        sa.Column('outcome', sa.String(), nullable=False),
        sa.Column('balance_after', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_spin_history_user_id', 'spin_history', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_spin_history_user_id', table_name='spin_history')
    op.drop_table('spin_history')
