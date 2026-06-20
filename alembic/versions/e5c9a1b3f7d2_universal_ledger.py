"""universal ledger: single source of truth for all balance changes

Replaces the naive per-player counters (players.total_won/total_lost) and the
spin_history table with one immutable `ledger` of signed, kinded events. Existing
balances are preserved by seeding one SIGNUP ("opening balance") row per player
equal to their current balance, so sum(ledger) == balance from day one.

Revision ID: e5c9a1b3f7d2
Revises: d4e8b1f0a2c7
Create Date: 2026-06-20

"""
import time
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e5c9a1b3f7d2'
down_revision: Union[str, Sequence[str], None] = 'd4e8b1f0a2c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ledger',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('memo', sa.String(), nullable=False, server_default=''),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ledger_user_id', 'ledger', ['user_id'])

    # Seed an opening-balance entry per existing player so the ledger reconstructs
    # their current balance. (Old win/loss history is intentionally not migrated.)
    op.get_bind().execute(
        sa.text(
            "INSERT INTO ledger (user_id, amount, kind, memo, created_at) "
            "SELECT user_id, balance, 'signup', 'opening balance', :ts FROM players"
        ),
        {"ts": time.time()},
    )

    op.drop_table('spin_history')

    with op.batch_alter_table('players') as batch:
        batch.drop_column('total_won')
        batch.drop_column('total_lost')


def downgrade() -> None:
    with op.batch_alter_table('players') as batch:
        batch.add_column(sa.Column('total_won', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('total_lost', sa.Integer(), nullable=False, server_default='0'))

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

    op.drop_index('ix_ledger_user_id', table_name='ledger')
    op.drop_table('ledger')
