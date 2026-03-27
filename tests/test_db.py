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
    db.apply_spin(1, -5)
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

def test_apply_spin_adds_positive_net():
    db.get_or_create(1, "alice")
    ok, new_bal = db.apply_spin(1, 50)
    assert ok is True
    assert new_bal == db.DEFAULT_BALANCE + 50


def test_apply_spin_subtracts_negative_net():
    db.get_or_create(1, "alice")
    ok, new_bal = db.apply_spin(1, -10)
    assert ok is True
    assert new_bal == db.DEFAULT_BALANCE - 10


def test_apply_spin_rejects_insufficient_funds():
    db.get_or_create(1, "alice")
    ok, bal = db.apply_spin(1, -(db.DEFAULT_BALANCE + 1))
    assert ok is False
    assert bal == db.DEFAULT_BALANCE  # unchanged


def test_apply_spin_allows_exact_drain_to_zero():
    db.get_or_create(1, "alice")
    ok, bal = db.apply_spin(1, -db.DEFAULT_BALANCE)
    assert ok is True
    assert bal == 0


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
