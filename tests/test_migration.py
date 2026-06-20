"""Migration test for the universal-ledger revision.

Unlike the other db tests (which build the schema directly via metadata), this
runs the real Alembic migration chain against a throwaway file DB, to prove the
opening-balance seed reconstructs every pre-existing player's balance.
"""
import os
import sqlite3

from alembic.config import Config
from alembic import command

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRE_LEDGER = "d4e8b1f0a2c7"   # revision just before the ledger
LEDGER = "e5c9a1b3f7d2"       # the universal-ledger revision
HEAD = "a7d2e4c9b1f3"         # player_groups (current head)


def _config(db_path) -> Config:
    cfg = Config(os.path.join(REPO_ROOT, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_ledger_migration_seeds_and_reconstructs_balances(tmp_path):
    db_path = tmp_path / "migrate.db"
    cfg = _config(db_path)

    # Build the pre-ledger schema and seed players with assorted balances.
    command.upgrade(cfg, PRE_LEDGER)
    con = sqlite3.connect(db_path)
    con.executemany(
        "INSERT INTO players (user_id, username, balance, total_won, total_lost) "
        "VALUES (?,?,?,?,?)",
        [(1, "alice", 250, 99, 12), (2, "bob", 0, 0, 0), (3, "carol", 1000, 5, 5)],
    )
    con.commit()
    con.close()

    # Apply the ledger migration.
    command.upgrade(cfg, LEDGER)

    con = sqlite3.connect(db_path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(players)")]
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    ledger_sums = dict(con.execute(
        "SELECT user_id, SUM(amount) FROM ledger GROUP BY user_id").fetchall())
    balances = dict(con.execute("SELECT user_id, balance FROM players").fetchall())
    kinds = {r[0] for r in con.execute("SELECT DISTINCT kind FROM ledger")}
    con.close()

    assert "total_won" not in cols and "total_lost" not in cols  # naive counters gone
    assert "spin_history" not in tables and "ledger" in tables
    assert ledger_sums == balances        # ledger reconstructs every balance
    assert balances == {1: 250, 2: 0, 3: 1000}
    assert kinds == {"signup"}            # every seed row is an opening balance


def test_full_chain_to_head_creates_player_groups(tmp_path):
    db_path = tmp_path / "head.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD)

    con = sqlite3.connect(db_path)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    cols = [r[1] for r in con.execute("PRAGMA table_info(player_groups)")]
    con.close()

    assert "player_groups" in tables
    assert set(cols) == {"user_id", "group_id", "first_seen", "last_seen"}


def test_ledger_migration_downgrade_restores_old_schema(tmp_path):
    db_path = tmp_path / "downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, LEDGER)
    command.downgrade(cfg, PRE_LEDGER)

    con = sqlite3.connect(db_path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(players)")]
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    con.close()

    assert "total_won" in cols and "total_lost" in cols
    assert "spin_history" in tables and "ledger" not in tables
