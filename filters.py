"""Custom telegram-bot MessageFilter implementations."""
from telegram.ext import filters

import db


class CasinoFilter(filters.MessageFilter):
    """Accept 🎰 dice only in private chats or in the configured casino topic.

    Groups can pin the bot to a single topic via /settopic.  Once set, spins
    sent outside that topic are silently ignored so the bot doesn't pollute
    other threads.  If no topic is configured the bot accepts spins anywhere
    in the group.
    """

    def filter(self, message) -> bool:  # type: ignore[override]
        # Always accept private chats.
        if message.chat.type == "private":
            return True
        if message.chat.type not in ("group", "supergroup"):
            return False

        locations = db.get_all_casino_locations()
        topic_id = locations.get(message.chat_id)
        if topic_id is None:
            # No topic restriction — accept anywhere in this group.
            return True
        return message.message_thread_id == topic_id
