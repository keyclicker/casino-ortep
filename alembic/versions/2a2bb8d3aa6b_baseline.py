"""baseline

Revision ID: 2a2bb8d3aa6b
Revises:
Create Date: 2026-03-27

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '2a2bb8d3aa6b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create original schema (players + config)."""
    op.create_table(
        'players',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('balance', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('user_id'),
    )
    op.create_table(
        'config',
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    """Drop original schema."""
    op.drop_table('config')
    op.drop_table('players')
