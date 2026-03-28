"""add pending_reveals table

Revision ID: c3f7a9e12d45
Revises: 18ce4489452e
Create Date: 2026-03-27

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c3f7a9e12d45'
down_revision: Union[str, Sequence[str], None] = '18ce4489452e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pending_reveals',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('chat_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('reveal_text', sa.String(), nullable=False),
        sa.Column('reveal_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('pending_reveals')
