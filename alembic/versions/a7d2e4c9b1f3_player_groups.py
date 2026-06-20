"""player_groups: many-to-many of which players play in which groups

Enables group-scoped leaderboards (/balances) while /all_balances stays global.

Revision ID: a7d2e4c9b1f3
Revises: e5c9a1b3f7d2
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a7d2e4c9b1f3'
down_revision: Union[str, Sequence[str], None] = 'e5c9a1b3f7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'player_groups',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('first_seen', sa.Float(), nullable=False),
        sa.Column('last_seen', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'group_id'),
    )
    op.create_index('ix_player_groups_group_id', 'player_groups', ['group_id'])


def downgrade() -> None:
    op.drop_index('ix_player_groups_group_id', table_name='player_groups')
    op.drop_table('player_groups')
