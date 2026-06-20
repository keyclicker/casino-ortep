"""Tests for the global error handler that surfaces handler exceptions."""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("BOT_TOKEN", "test:token")  # bot.py checks this at import

from telegram import Update
import bot


def run(coro):
    return asyncio.run(coro)


def test_on_error_notifies_user():
    msg = MagicMock(); msg.reply_text = AsyncMock()
    update = MagicMock(spec=Update); update.effective_message = msg
    ctx = MagicMock(); ctx.error = RuntimeError("boom")
    run(bot._on_error(update, ctx))
    msg.reply_text.assert_awaited_once()


def test_on_error_tolerates_non_update():
    ctx = MagicMock(); ctx.error = RuntimeError("boom")
    run(bot._on_error("not-an-update", ctx))  # must not raise


def test_on_error_survives_reply_failure():
    msg = MagicMock(); msg.reply_text = AsyncMock(side_effect=RuntimeError("send failed"))
    update = MagicMock(spec=Update); update.effective_message = msg
    ctx = MagicMock(); ctx.error = ValueError("boom")
    run(bot._on_error(update, ctx))  # swallows the secondary failure
