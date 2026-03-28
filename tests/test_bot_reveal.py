"""Tests for the spoiler reveal flow in bot.py."""
import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from sqlalchemy import create_engine

# BOT_TOKEN must be set before importing bot
os.environ.setdefault("BOT_TOKEN", "0:test")

import db
from db import Base
from telegram import MessageEntity

import bot
from bot import _reveal_message, handle_slot, SPOILER_DELAY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def _make_reveal_context(chat_id=100, message_id=200, reveal_text="desc\n💰 text", reveal_id=1):
    ctx = MagicMock()
    ctx.bot.edit_message_text = AsyncMock()
    ctx.job.data = (chat_id, message_id, reveal_text, reveal_id)
    return ctx


def _make_slot_context():
    ctx = MagicMock()
    ctx.application.job_queue.run_once = MagicMock()
    return ctx


def _make_slot_update(dice_value=64, user_id=1, username="tester"):
    user = MagicMock()
    user.id = user_id
    user.username = username
    user.first_name = "Tester"

    sent_msg = MagicMock()
    sent_msg.chat_id = -999
    sent_msg.message_id = 555

    msg = MagicMock()
    msg.dice.value = dice_value
    msg.reply_text = AsyncMock(return_value=sent_msg)

    update = MagicMock()
    update.effective_message = msg
    update.effective_user = user
    return update, msg


@pytest.fixture(autouse=True)
def in_memory_db():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    with patch.object(db, "engine", test_engine):
        yield test_engine


# ---------------------------------------------------------------------------
# _reveal_message
# ---------------------------------------------------------------------------

class TestRevealMessage:
    def test_edits_message_with_correct_args(self):
        ctx = _make_reveal_context(chat_id=100, message_id=200, reveal_text="bold *text*", reveal_id=7)
        db.add_pending_reveal(100, 200, "bold *text*", 0.0)

        run(_reveal_message(ctx))

        ctx.bot.edit_message_text.assert_awaited_once_with(
            chat_id=100,
            message_id=200,
            text="bold *text*",
            parse_mode="Markdown",
        )

    def test_deletes_db_row_on_success(self):
        rid = db.add_pending_reveal(100, 200, "text", 0.0)
        ctx = _make_reveal_context(reveal_id=rid)

        run(_reveal_message(ctx))

        assert db.get_pending_reveals() == []

    def test_deletes_db_row_even_when_edit_fails(self):
        rid = db.add_pending_reveal(100, 200, "text", 0.0)
        ctx = _make_reveal_context(reveal_id=rid)
        ctx.bot.edit_message_text.side_effect = Exception("message not modified")

        run(_reveal_message(ctx))  # must not raise

        assert db.get_pending_reveals() == []


# ---------------------------------------------------------------------------
# handle_slot — spoiler sending
# ---------------------------------------------------------------------------

class TestHandleSlotSpoiler:
    def _run_slot(self, dice_value=64, balance=100, new_balance=490):
        update, msg = _make_slot_update(dice_value=dice_value)
        ctx = _make_slot_context()
        with patch.object(db, "get_or_create", return_value=balance), \
             patch.object(db, "apply_spin", return_value=(True, new_balance)):
            run(handle_slot(update, ctx))
        return msg, ctx

    def test_reply_uses_spoiler_entity(self):
        msg, _ = self._run_slot()
        _, kwargs = msg.reply_text.call_args
        entities = kwargs["entities"]
        assert len(entities) == 1
        assert entities[0].type == MessageEntity.SPOILER
        assert entities[0].offset == 0

    def test_spoiler_entity_length_matches_utf16(self):
        msg, _ = self._run_slot()
        text, kwargs = msg.reply_text.call_args.args[0], msg.reply_text.call_args.kwargs
        expected_len = len(text.encode("utf-16-le")) // 2
        assert kwargs["entities"][0].length == expected_len

    def test_reply_is_silent(self):
        msg, _ = self._run_slot()
        _, kwargs = msg.reply_text.call_args
        assert kwargs.get("disable_notification") is True

    def test_adds_pending_reveal_to_db(self):
        self._run_slot()
        reveals = db.get_pending_reveals()
        assert len(reveals) == 1
        r = reveals[0]
        assert r.chat_id == -999
        assert r.message_id == 555
        assert isinstance(r.reveal_text, str)
        assert len(r.reveal_text) > 0

    def test_reveal_text_contains_markdown_bold(self):
        self._run_slot()
        reveal_text = db.get_pending_reveals()[0].reveal_text
        assert "*" in reveal_text

    def test_reveal_text_differs_from_spoiler_text(self):
        msg, _ = self._run_slot()
        spoiler_text = msg.reply_text.call_args.args[0]
        reveal_text = db.get_pending_reveals()[0].reveal_text
        assert spoiler_text != reveal_text

    def test_schedules_reveal_job(self):
        _, ctx = self._run_slot()
        ctx.application.job_queue.run_once.assert_called_once()
        call_kwargs = ctx.application.job_queue.run_once.call_args.kwargs
        assert call_kwargs["when"] == SPOILER_DELAY

    def test_job_data_matches_db_row(self):
        _, ctx = self._run_slot()
        reveals = db.get_pending_reveals()
        job_data = ctx.application.job_queue.run_once.call_args.kwargs["data"]
        assert job_data[0] == reveals[0].chat_id
        assert job_data[1] == reveals[0].message_id
        assert job_data[2] == reveals[0].reveal_text
        assert job_data[3] == reveals[0].id

    def test_insufficient_funds_does_not_store_reveal(self):
        update, msg = _make_slot_update()
        ctx = _make_slot_context()
        with patch.object(db, "get_or_create", return_value=5), \
             patch.object(db, "apply_spin", return_value=(False, 5)):
            run(handle_slot(update, ctx))
        assert db.get_pending_reveals() == []
        ctx.application.job_queue.run_once.assert_not_called()
