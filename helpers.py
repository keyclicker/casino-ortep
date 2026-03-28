"""Shared constants and utility helpers used across bot modules."""
import logging

from telegram import Message, Update
from telegram.helpers import escape_markdown

import db

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

BOT_ADMIN = "nick_keyclicker"
SPOILER_DELAY = 2  # seconds before a slot-result spoiler is lifted

# ── Display ────────────────────────────────────────────────────────────────

def display_name(user) -> str:
    """Return @username if available, otherwise the user's first name."""
    return f"@{user.username}" if user.username else user.first_name


def md_name(user) -> str:
    """Markdown-safe display name (escapes _, *, `, [ characters)."""
    return escape_markdown(display_name(user))


def ensure_player(user) -> int:
    """Register the user if new and return their current balance."""
    return db.get_or_create(user.id, user.username or "")


# ── Permissions ────────────────────────────────────────────────────────────

def is_bot_admin(user) -> bool:
    """Return True if the user is the hard-coded bot administrator."""
    return (user.username or "").lower() == BOT_ADMIN.lower()


async def is_chat_admin(update: Update, context) -> bool:
    """Return True if the calling user is an admin of the current chat."""
    member = await context.bot.get_chat_member(
        update.effective_chat.id, update.effective_user.id
    )
    return member.status in ("administrator", "creator")


# ── Messaging ─────────────────────────────────────────────────────────────

async def reply(msg: Message, text: str, **kwargs) -> None:
    """Send a silent reply to msg (no notification sound)."""
    await msg.reply_text(text, disable_notification=True, **kwargs)
