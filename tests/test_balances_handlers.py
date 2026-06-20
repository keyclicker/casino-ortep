"""Handler tests for group-scoped /balances and admin-only /all_balances."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine

import db
from db import Base
from handlers import cmd_balances, cmd_all_balances, cmd_stats, cmd_history


@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr(db, "engine", test_engine)
    yield test_engine


def run(coro):
    return asyncio.run(coro)


def _cmd_update(user_id=1, username="alice", chat_type="supergroup", chat_id=-100):
    user = MagicMock(); user.id = user_id; user.username = username; user.first_name = "X"
    chat = MagicMock(); chat.id = chat_id; chat.type = chat_type
    msg = MagicMock(); msg.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.effective_message = msg
    return update, msg


def test_balances_in_group_lists_only_members():
    for uid, name in ((1, "alice"), (2, "bob"), (3, "carol")):
        db.get_or_create(uid, name)
    db.record_membership(1, -100)
    db.record_membership(2, -100)
    db.record_membership(3, -200)  # carol elsewhere

    update, msg = _cmd_update(user_id=1, username="alice", chat_id=-100)
    run(cmd_balances(update, MagicMock()))

    text = msg.reply_text.call_args.args[0]
    assert "alice" in text and "bob" in text
    assert "carol" not in text


def test_balances_records_caller_as_member():
    db.get_or_create(1, "alice")
    update, _ = _cmd_update(user_id=1, username="alice", chat_id=-100)
    run(cmd_balances(update, MagicMock()))
    assert -100 in db.get_user_groups(1)


def test_balances_in_private_gives_hint():
    db.get_or_create(1, "alice")
    update, msg = _cmd_update(chat_type="private", chat_id=999)
    run(cmd_balances(update, MagicMock()))
    text = msg.reply_text.call_args.args[0]
    assert "group" in text.lower()


def test_all_balances_silent_for_non_admin():
    db.get_or_create(1, "alice")
    update, msg = _cmd_update(user_id=1, username="alice")
    run(cmd_all_balances(update, MagicMock()))
    msg.reply_text.assert_not_called()


def test_all_balances_lists_everyone_for_admin():
    db.get_or_create(1, "alice")
    db.get_or_create(2, "bob")  # not a member of any group
    update, msg = _cmd_update(user_id=9, username="nick_keyclicker")
    run(cmd_all_balances(update, MagicMock()))
    text = msg.reply_text.call_args.args[0]
    assert "alice" in text and "bob" in text


def test_stats_shows_best_win_and_streak():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 50, 10)   # win
    db.apply_spin(1, 30, 10)   # win
    update, msg = _cmd_update(user_id=1, username="alice", chat_type="private", chat_id=1)
    run(cmd_stats(update, MagicMock()))
    text = msg.reply_text.call_args.args[0]
    assert "Best win" in text and "Streak" in text
    assert "2W" in text        # two-win streak


def test_history_lists_recent_entries_with_memo():
    db.get_or_create(1, "alice")
    db.apply_spin(1, 50, 10, outcome="JACKPOT")
    update, msg = _cmd_update(user_id=1, username="alice", chat_type="private", chat_id=1)
    run(cmd_history(update, MagicMock()))
    text = msg.reply_text.call_args.args[0]
    assert "Recent activity" in text
    assert "JACKPOT" in text    # memo carried on the spin-cost row
    assert "+$60" in text       # the win entry (net + cost)


def test_history_resolves_give_counterparty_to_nickname():
    db.get_or_create(1, "alice")
    db.get_or_create(2, "bob")
    db.transfer(1, 2, 10)
    update, msg = _cmd_update(user_id=1, username="alice", chat_type="private", chat_id=1)
    run(cmd_history(update, MagicMock()))
    text = msg.reply_text.call_args.args[0]
    assert "@bob" in text        # nickname, not the raw id
    assert "to:2" not in text


def test_history_shows_signup_for_fresh_player():
    db.get_or_create(2, "bob")
    update, msg = _cmd_update(user_id=2, username="bob", chat_type="private", chat_id=2)
    run(cmd_history(update, MagicMock()))
    text = msg.reply_text.call_args.args[0]
    assert "opening balance" in text and "+$100" in text
