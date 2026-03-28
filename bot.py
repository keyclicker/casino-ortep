"""Telegram bot entry point — wires up handlers, jobs, and starts polling."""
import logging
import os
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

import db
from filters import CasinoFilter
from handlers import (
    handle_slot,
    cmd_give, cmd_stats,
    cmd_settopic, cmd_unsettopic, cmd_casino, cmd_casinostats,
    cmd_dodep, cmd_balances,
)
from jobs import job_hourly_deposit, reveal_message

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "Error: BOT_TOKEN is not set. "
        "Copy .env.example to .env and fill it in."
    )

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _post_init(app) -> None:
    """Register bot commands, schedule jobs, and re-queue pending reveals."""
    await app.bot.set_my_commands([
        ("stats",       "Show your balance and win/loss statistics"),
        ("give",        "Send coins to another player"),
        ("casinostats", "Show casino win/loss totals"),
        ("casino",      "Show where the casino is active"),
        ("settopic",    "Set this topic as the casino (admins only)"),
        ("unsettopic",  "Remove the casino topic (admins only)"),
    ])

    # Schedule the hourly deposit to fire at the next full hour boundary.
    now       = datetime.now(timezone.utc)
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    app.job_queue.run_repeating(job_hourly_deposit, interval=3600, first=next_hour)

    # Re-queue any spoiler reveals that were pending before the last restart.
    pending = db.get_pending_reveals()
    if pending:
        now_ts = time.time()
        for r in pending:
            delay = max(0.0, r.reveal_at - now_ts)
            app.job_queue.run_once(
                reveal_message,
                when=delay,
                data=(r.chat_id, r.message_id, r.reveal_text, r.id),
            )
        logger.info("Scheduled %d pending reveal(s) from previous run", len(pending))

    bot_info = await app.bot.get_me()
    logger.info("Bot ready: @%s (id=%d)", bot_info.username, bot_info.id)


def main() -> None:
    """Initialise the DB, register all handlers, and start long-polling."""
    db.init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(_post_init).build()

    # Slot machine — must come before command handlers so it's checked first.
    app.add_handler(MessageHandler(
        CasinoFilter() & filters.Dice.SLOT_MACHINE & ~filters.FORWARDED,
        handle_slot,
    ))

    # Player commands
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("casinostats", cmd_casinostats))
    app.add_handler(CommandHandler("give",        cmd_give))

    # Group-admin commands
    app.add_handler(CommandHandler("settopic",    cmd_settopic))
    app.add_handler(CommandHandler("unsettopic",  cmd_unsettopic))
    app.add_handler(CommandHandler("casino",      cmd_casino))

    # Bot-admin commands (silently ignored for non-admins)
    app.add_handler(CommandHandler("dodep",       cmd_dodep))
    app.add_handler(CommandHandler("balances",    cmd_balances))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
