import pytest
from sqlalchemy import create_engine

import db
from db import Base, Player


@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch):
    """Replace the module-level engine with an in-memory SQLite engine for each test."""
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr(db, "engine", test_engine)
    yield test_engine


# --- get_or_create ---

def test_get_or_create_new_player_gets_default_balance():
    balance = db.get_or_create(1, "alice")
    assert balance == db.DEFAULT_BALANCE


def test_get_or_create_existing_player_returns_current_balance():
    db.get_or_create(1, "alice")
    db.apply_spin(1, -5, COST)
    balance = db.get_or_create(1, "alice")
    assert balance == db.DEFAULT_BALANCE - 5


def test_get_or_create_updates_username():
    db.get_or_create(1, "old_name")
    db.get_or_create(1, "new_name")
    player = db.get_by_username("new_name")
    assert player is not None
    assert player.user_id == 1


# --- get_balance ---

def test_get_balance_unknown_player_returns_none():
    assert db.get_balance(999) is None


def test_get_balance_known_player():
    db.get_or_create(1, "alice")
    assert db.get_balance(1) == db.DEFAULT_BALANCE


# --- apply_spin ---

COST = 10  # SPIN_COST used in tests


def test_apply_spin_adds_positive_net():
    db.get_or_create(1, "alice")
    ok, new_bal = db.apply_spin(1, 50, COST)
    assert ok is True
    assert new_bal == db.DEFAULT_BALANCE + 50


def test_apply_spin_subtracts_negative_net():
    db.get_or_create(1, "alice")
    ok, new_bal = db.apply_spin(1, -COST, COST)
    assert ok is True
    assert new_bal == db.DEFAULT_BALANCE - COST


def test_apply_spin_rejects_when_balance_below_cost():
    db.get_or_create(1, "alice")
    # Give alice only $5 (below spin cost of $10)
    db.apply_spin(1, -(db.DEFAULT_BALANCE - 5), COST)
    ok, bal = db.apply_spin(1, -COST, COST)
    assert ok is False
    assert bal == 5  # unchanged


def test_apply_spin_rejects_partial_win_when_balance_below_cost():
    # Bug regression: balance=$5, net=-2 (partial win) must still be rejected
    db.get_or_create(1, "alice")
    db.apply_spin(1, -(db.DEFAULT_BALANCE - 5), COST)
    ok, bal = db.apply_spin(1, -2, COST)  # two sevens: net=-2
    assert ok is False
    assert bal == 5  # unchanged


def test_apply_spin_allows_exact_cost():
    db.get_or_create(1, "alice")
    # Set balance to exactly COST
    db.apply_spin(1, -(db.DEFAULT_BALANCE - COST), COST)
    ok, bal = db.apply_spin(1, -COST, COST)
    assert ok is True
    assert bal == 0


# --- ledger (source of truth) ---

def _entries(user_id, kind=None):
    rows = db.get_ledger(user_id, limit=1000)
    return [r for r in rows if kind is None or r.kind == kind]


def test_signup_writes_opening_balance_entry():
    db.get_or_create(1, "alice")
    signups = _entries(1, db.LedgerKind.SIGNUP)
    assert len(signups) == 1
    assert signups[0].amount == db.DEFAULT_BALANCE


def test_apply_spin_win_decomposes_into_cost_and_win():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 50, COST, outcome="JACKPOT!")  # net +50, price 10 → payout 60
    cost = _entries(1, db.LedgerKind.SPIN_COST)
    win = _entries(1, db.LedgerKind.SPIN_WIN)
    assert cost[0].amount == -COST
    assert cost[0].memo == "JACKPOT!"
    assert win[0].amount == 60                  # net + cost
    assert db.get_balance(1) == db.DEFAULT_BALANCE + 50


def test_apply_spin_penalty_decomposes_into_cost_and_penalty():
    db.get_or_create(1, "alice")
    db.apply_spin(1, -40, COST, outcome="PENALTY!")  # net -40, price 10
    assert _entries(1, db.LedgerKind.SPIN_COST)[0].amount == -COST
    assert _entries(1, db.LedgerKind.SPIN_PENALTY)[0].amount == -30  # net + cost
    assert db.get_balance(1) == db.DEFAULT_BALANCE - 40


def test_break_even_spin_writes_only_cost():
    db.get_or_create(1, "alice")
    db.apply_spin(1, -COST, COST)  # net == -cost → no win/penalty entry
    assert len(_entries(1, db.LedgerKind.SPIN_WIN)) == 0
    assert len(_entries(1, db.LedgerKind.SPIN_PENALTY)) == 0
    assert len(_entries(1, db.LedgerKind.SPIN_COST)) == 1


def test_rejected_spin_writes_no_ledger_entries():
    db.get_or_create(1, "alice")
    db.apply_spin(1, -(db.DEFAULT_BALANCE - 5), COST)  # leaves balance 5 < COST
    before = len(db.get_ledger(1, limit=1000))
    ok, _ = db.apply_spin(1, -COST, COST)
    assert ok is False
    assert len(db.get_ledger(1, limit=1000)) == before


def test_transfer_writes_give_entries():
    db.get_or_create(1, "alice")
    db.get_or_create(2, "bob")
    db.transfer(1, 2, 30)
    assert _entries(1, db.LedgerKind.GIVE_OUT)[0].amount == -30
    assert _entries(2, db.LedgerKind.GIVE_IN)[0].amount == 30


def test_credit_writes_admin_dodep_entry():
    db.get_or_create(1, "alice")
    db.credit(1, 500)
    assert _entries(1, db.LedgerKind.DODEP_ADMIN)[0].amount == 500


def test_daily_deposit_writes_casino_dodep_for_each_player():
    db.get_or_create(1, "alice")
    db.get_or_create(2, "bob")
    db.daily_deposit(20)
    assert _entries(1, db.LedgerKind.DODEP_CASINO)[0].amount == 20
    assert _entries(2, db.LedgerKind.DODEP_CASINO)[0].amount == 20


def test_get_ledger_newest_first_and_limited():
    db.get_or_create(1, "alice")
    for _ in range(5):
        db.apply_spin(1, 5, 0)  # win-only spins
    rows = db.get_ledger(1, limit=3)
    assert len(rows) == 3
    assert rows[0].id > rows[1].id > rows[2].id


# --- balances are recreatable from the ledger ---

def test_balance_always_equals_ledger_sum():
    db.get_or_create(1, "alice")
    db.get_or_create(2, "bob")
    db.apply_spin(1, 50, COST)    # win
    db.apply_spin(1, -40, COST)   # penalty
    db.apply_spin(1, -COST, COST)  # break-even (cost only, no win/penalty row)
    db.transfer(1, 2, 15)
    db.credit(2, 100)
    db.daily_deposit(20)
    for uid in (1, 2):
        ledger_sum = sum(r.amount for r in db.get_ledger(uid, limit=10_000))
        assert db.get_balance(uid) == ledger_sum


def test_recompute_balance_repairs_cache():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 50, COST)
    # Corrupt the cached balance directly, then rebuild from the ledger.
    with db.Session(db.engine) as s:
        s.get(db.Player, 1).balance = 99999
        s.commit()
    assert db.recompute_balance(1) == db.DEFAULT_BALANCE + 50
    assert db.get_balance(1) == db.DEFAULT_BALANCE + 50


# --- stats from the ledger ---

def test_get_player_stats_from_ledger():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 50, COST)    # cost -10, win +60
    db.apply_spin(1, -40, COST)   # cost -10, penalty -30
    won, lost = db.get_player_stats(1)
    assert won == 60              # gross payouts
    assert lost == 10 + 10 + 30   # prices + penalty


def test_get_player_stats_unknown_returns_none():
    assert db.get_player_stats(999) is None


def test_get_player_stats_ignores_non_spin_entries():
    # Signups, gives and dodeps must never count toward won/lost.
    db.get_or_create(1, "alice")   # signup +100
    db.get_or_create(2, "bob")
    db.credit(1, 500)              # admin dodep
    db.transfer(2, 1, 30)          # give received
    db.daily_deposit(20)           # casino dodep
    assert db.get_player_stats(1) == (0, 0)   # registered, but never spun


def test_get_casino_stats_from_ledger():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 50, COST)
    db.apply_spin(1, -40, COST)
    paid_out, collected, total_balance = db.get_casino_stats()
    assert paid_out == 60
    assert collected == 50        # 10 + 10 + 30
    assert total_balance == db.get_balance(1)


def test_highlights_biggest_win_and_win_streak():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 50, COST)    # win, payout 60
    db.apply_spin(1, 200, COST)   # win, payout 210 (biggest)
    db.apply_spin(1, 30, COST)    # win, payout 40
    best, length, is_win = db.get_player_highlights(1)
    assert best == 210
    assert (length, is_win) == (3, True)


def test_highlights_loss_streak():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 50, COST)    # win
    db.apply_spin(1, -40, COST)   # loss
    db.apply_spin(1, -5, COST)    # loss
    best, length, is_win = db.get_player_highlights(1)
    assert best == 60             # the one win's payout
    assert (length, is_win) == (2, False)


def test_highlights_ignore_non_spin_entries():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 50, COST)    # win
    db.credit(1, 999)             # dodep — not a spin, must not break the streak
    db.daily_deposit(20)          # casino dodep — same
    db.apply_spin(1, 30, COST)    # win
    best, length, is_win = db.get_player_highlights(1)
    assert best == 60
    assert (length, is_win) == (2, True)   # deposits between wins don't reset


def test_highlights_no_spins():
    db.get_or_create(1, "alice")
    db.credit(1, 100)
    assert db.get_player_highlights(1) == (0, 0, False)


def test_get_leaderboard_reports_won_lost():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 50, COST)
    rows = db.get_leaderboard()
    assert len(rows) == 1
    player, won, lost = rows[0]
    assert player.user_id == 1
    assert won == 60
    assert lost == 10


# --- player ↔ group membership ---

def test_record_membership_is_idempotent_and_refreshes_last_seen():
    db.record_membership(1, -100, ts=10.0)
    db.record_membership(1, -100, ts=20.0)   # same pair again
    assert db.get_user_groups(1) == [-100]   # one row, not two
    with db.Session(db.engine) as s:
        row = s.get(db.PlayerGroup, (1, -100))
        assert row.first_seen == 10.0 and row.last_seen == 20.0


def test_membership_is_many_to_many():
    db.record_membership(1, -100)
    db.record_membership(1, -200)
    db.record_membership(2, -100)
    assert sorted(db.get_user_groups(1)) == [-200, -100]
    assert sorted(db.get_group_members(-100)) == [1, 2]
    assert db.get_group_members(-999) == []


def test_get_leaderboard_filters_to_group_members():
    for uid, name in ((1, "alice"), (2, "bob"), (3, "carol")):
        db.get_or_create(uid, name)
    db.record_membership(1, -100)
    db.record_membership(2, -100)
    # carol (3) is in a different group only
    db.record_membership(3, -200)

    here = db.get_leaderboard(-100)
    assert {p.user_id for p, _, _ in here} == {1, 2}      # carol excluded
    everyone = db.get_leaderboard()                        # global, unfiltered
    assert {p.user_id for p, _, _ in everyone} == {1, 2, 3}


def test_get_leaderboard_empty_group():
    db.get_or_create(1, "alice")
    assert db.get_leaderboard(-555) == []


# --- spin signals (cdm inputs) ---

def test_get_spin_signals_empty():
    db.get_or_create(1, "alice")
    assert db.get_spin_signals(1, 12) == (0, 0, 0)


def test_get_spin_signals_aggregates():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 30, 10, outcome="win")    # net +30, cost 10
    db.apply_spin(1, -20, 10, outcome="loss")  # net -20, cost 10
    recent_net, life_net, life_staked = db.get_spin_signals(1, 12)
    assert recent_net == 10          # 30 - 20
    assert life_net == 30 - 20       # total_won - total_lost
    assert life_staked == 20         # 10 + 10


def test_get_spin_signals_recent_window_limits():
    db.get_or_create(1, "alice")
    for _ in range(20):
        db.apply_spin(1, 1, 0, outcome="x")  # 20 spins of net +1
    recent_net, _, _ = db.get_spin_signals(1, 12)
    assert recent_net == 12          # only the last 12 counted


def test_get_spin_signals_window_ignores_interleaved_non_spin_rows():
    # The recent-window threshold is a SPIN_COST id; deposits/gives sitting
    # between spins must neither shift the window nor pollute the net.
    db.get_or_create(1, "alice")
    db.get_or_create(2, "bob")
    for i in range(15):
        db.apply_spin(1, 5, 10, outcome="w")   # net +5 (cost -10, win +15)
        if i % 3 == 0:
            db.daily_deposit(7)                # casino dodep (non-spin, hits user 1 too)
            db.transfer(1, 2, 1)               # give out (non-spin)
    recent_net, life_net, life_staked = db.get_spin_signals(1, 12)
    assert recent_net == 12 * 5      # last 12 spins only; dodeps/gives excluded
    assert life_net == 15 * 5        # every spin
    assert life_staked == 15 * 10    # total prices paid, never the deposits


# --- transfer ---

def test_transfer_moves_funds():
    db.get_or_create(1, "alice")
    db.get_or_create(2, "bob")
    from_bal, to_bal = db.transfer(1, 2, 30)
    assert from_bal == db.DEFAULT_BALANCE - 30
    assert to_bal == db.DEFAULT_BALANCE + 30


def test_transfer_insufficient_funds_raises():
    db.get_or_create(1, "alice")
    db.get_or_create(2, "bob")
    with pytest.raises(ValueError, match="insufficient_funds"):
        db.transfer(1, 2, db.DEFAULT_BALANCE + 1)


def test_transfer_insufficient_funds_leaves_balances_unchanged():
    db.get_or_create(1, "alice")
    db.get_or_create(2, "bob")
    with pytest.raises(ValueError):
        db.transfer(1, 2, db.DEFAULT_BALANCE + 1)
    assert db.get_balance(1) == db.DEFAULT_BALANCE
    assert db.get_balance(2) == db.DEFAULT_BALANCE


def test_transfer_unknown_sender_raises():
    db.get_or_create(2, "bob")
    with pytest.raises(ValueError, match="insufficient_funds"):
        db.transfer(999, 2, 10)


def test_transfer_exact_balance_succeeds():
    db.get_or_create(1, "alice")
    db.get_or_create(2, "bob")
    from_bal, _ = db.transfer(1, 2, db.DEFAULT_BALANCE)
    assert from_bal == 0


# --- get_by_username ---

def test_get_by_username_found():
    db.get_or_create(1, "alice")
    player = db.get_by_username("alice")
    assert player is not None
    assert player.user_id == 1


def test_get_by_username_strips_at_sign():
    db.get_or_create(1, "alice")
    player = db.get_by_username("@alice")
    assert player is not None


def test_get_by_username_not_found():
    assert db.get_by_username("nobody") is None


# --- casino location ---

def test_get_casino_location_not_registered():
    assert db.get_casino_location(-100) is None


def test_set_and_get_casino_location():
    db.set_casino_location(-100, 42)
    assert db.get_casino_location(-100) == 42


def test_set_casino_location_updates_topic():
    db.set_casino_location(-100, 1)
    db.set_casino_location(-100, 2)
    assert db.get_casino_location(-100) == 2


def test_multiple_groups_independent():
    db.set_casino_location(-100, 1)
    db.set_casino_location(-200, 2)
    assert db.get_casino_location(-100) == 1
    assert db.get_casino_location(-200) == 2


def test_get_all_casino_locations_empty():
    assert db.get_all_casino_locations() == {}


def test_get_all_casino_locations():
    db.set_casino_location(-100, 1)
    db.set_casino_location(-200, 2)
    assert db.get_all_casino_locations() == {-100: 1, -200: 2}


def test_clear_casino_location_removes_only_target_group():
    db.set_casino_location(-100, 1)
    db.set_casino_location(-200, 2)
    db.clear_casino_location(-100)
    assert db.get_casino_location(-100) is None
    assert db.get_casino_location(-200) == 2


def test_clear_casino_location_idempotent():
    db.clear_casino_location(-100)
    db.clear_casino_location(-100)
    assert db.get_casino_location(-100) is None
