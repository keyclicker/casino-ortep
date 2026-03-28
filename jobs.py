"""Background jobs: spoiler reveal and hourly deposit."""
import logging

from telegram.ext import ContextTypes

import db
from casino import HOURLY_DEPOSIT

logger = logging.getLogger(__name__)


async def reveal_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit a slot-result message to remove its spoiler.

    The job is scheduled SPOILER_DELAY seconds after the initial send.  It
    is also re-scheduled on startup for any rows left in pending_reveals,
    so reveals survive a bot restart.

    Job data tuple: (chat_id, message_id, reveal_text, db_reveal_id)
    """
    chat_id, message_id, reveal_text, reveal_id = context.job.data
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=reveal_text,
            parse_mode="Markdown",
        )
    except Exception as exc:  # pylint: disable=broad-except
        # The message may have been deleted or permissions revoked.
        # Nothing actionable — log and fall through to clean up the DB row.
        logger.warning(
            "Failed to reveal msg %d in chat %d: %s", message_id, chat_id, exc
        )
    finally:
        db.delete_pending_reveal(reveal_id)


async def job_hourly_deposit(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Credit every player with HOURLY_DEPOSIT and announce it in casino topics."""
    count = db.daily_deposit(HOURLY_DEPOSIT)
    logger.info("Hourly deposit: +$%d credited to %d players", HOURLY_DEPOSIT, count)
    if not count:
        return

    for group_id, topic_id in db.get_all_casino_locations().items():
        await context.bot.send_message(
            chat_id=group_id,
            message_thread_id=topic_id,
            text=f"🎁 Dodep: *+${HOURLY_DEPOSIT}* credited to all {count} players!",
            parse_mode="Markdown",
            disable_notification=True,
        )
