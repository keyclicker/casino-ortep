"""Telegram bot entry point — registers handlers and starts polling."""
import logging
import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.helpers import escape_markdown
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

import db
from casino import HOURLY_DEPOSIT, SPIN_COST, calculate_score

load_dotenv()

BOT_ADMIN = "nick_keyclicker"
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "Error: BOT_TOKEN environment variable is not set. "
        "Copy .env.example to .env and fill it in."
    )

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# --- Filters ---

class CasinoFilter(filters.MessageFilter):
    """Accept 🎰 dice in private chats or in the configured casino group topic."""
    def filter(self, message):
        if message.chat.type == "private":
            return True
        if message.chat.type not in ("group", "supergroup"):
            return False
        locations = db.get_all_casino_locations()
        topic_id = locations.get(message.chat_id)
        if topic_id is None:
            return True  # no topic set for this group — accept anywhere
        return message.message_thread_id == topic_id


# --- Helpers ---

def _display_name(user) -> str:
    return f"@{user.username}" if user.username else user.first_name


def _md_name(user) -> str:
    """Display name safe for Markdown v1 (escapes _, *, `, [)."""
    return escape_markdown(_display_name(user))


def _ensure_player(user) -> int:
    """Register user if new and return their current balance."""
    return db.get_or_create(user.id, user.username or "")


async def _is_admin(update: Update, context) -> bool:
    member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    return member.status in ("administrator", "creator")


# --- Slot handler ---

async def handle_slot(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle an incoming 🎰 dice message."""
    msg = update.effective_message
    user = update.effective_user
    name = _display_name(user)

    _ensure_player(user)

    net, description = calculate_score(msg.dice.value)
    ok, new_balance = db.apply_spin(user.id, net)
    if not ok:
        logger.warning("uid=%d %s tried to spin with insufficient funds ($%d)",
                       user.id, name, new_balance)
        await msg.reply_text(
            f"❌ Not enough coins to spin!\n"
            f"💰 *${new_balance}* · need *${SPIN_COST}*",
            parse_mode="Markdown",
        )
        return

    sign = "+" if net >= 0 else "−"
    await msg.reply_text(
        f"{description}\n"
        f"*{sign}${abs(net)}* · 💰 *${new_balance}*",
        parse_mode="Markdown",
    )
    logger.info("uid=%d %s rolled 🎰 → $%+d | balance $%d", user.id, name, net, new_balance)


# --- Player commands ---

async def cmd_balance(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the caller's current balance."""
    user = update.effective_user
    balance = _ensure_player(user)
    logger.info("uid=%d %s checked balance: $%d", user.id, _display_name(user), balance)
    await update.effective_message.reply_text(
        f"💰 *${balance}*",
        parse_mode="Markdown",
    )


async def cmd_give(  # pylint: disable=too-many-locals,too-many-return-statements
    update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transfer funds to another player: /give @username amount."""
    msg = update.effective_message
    user = update.effective_user
    _ensure_player(user)

    args = context.args
    reply = msg.reply_to_message

    # Resolve target and amount
    if reply and len(args) == 1:
        # /give amount  (reply to target)
        amount_str = args[0]
        target_user = reply.from_user
        target_id = target_user.id
        safe_target = escape_markdown(_display_name(target_user))
        if db.get_balance(target_id) is None:
            await msg.reply_text(
                f"❌ {safe_target} not found — they need to spin at least once first.",
                parse_mode="Markdown",
            )
            return
    elif len(args) == 2:
        # /give @username amount
        amount_str = args[1]
        target_username = args[0].lstrip("@")
        target = db.get_by_username(target_username)
        if target is None:
            logger.warning("uid=%d %s tried to give to unknown player @%s",
                           user.id, _display_name(user), target_username)
            await msg.reply_text(
                f"❌ {escape_markdown('@' + target_username)} not found"
                " — they need to spin at least once first.",
                parse_mode="Markdown",
            )
            return
        target_id = target.user_id
        safe_target = escape_markdown(f"@{target_username}")
    else:
        await msg.reply_text(
            "Usage: `/give @username amount` or reply to a user with `/give amount`",
            parse_mode="Markdown",
        )
        return

    try:
        amount = int(amount_str)
    except ValueError:
        await msg.reply_text("❌ Amount must be a whole number.")
        return

    if amount <= 0:
        await msg.reply_text("❌ Amount must be positive.")
        return

    if target_id == user.id:
        await msg.reply_text("❌ You can't give money to yourself.")
        return

    try:
        from_bal, to_bal = db.transfer(user.id, target_id, amount)
    except ValueError:
        balance = db.get_balance(user.id)
        logger.warning("uid=%d %s failed to give $%d (insufficient funds, balance $%d)",
                       user.id, _display_name(user), amount, balance)
        await msg.reply_text(
            f"❌ Not enough funds · 💰 *${balance}*",
            parse_mode="Markdown",
        )
        return

    logger.info("uid=%d %s gave $%d to uid=%d | balances $%d → $%d",
                user.id, _display_name(user), amount, target_id, from_bal, to_bal)
    await msg.reply_text(
        f"✅ {_md_name(user)} → {safe_target} · *${amount}*\n"
        f"💰 You: *${from_bal}* · {safe_target}: *${to_bal}*",
        parse_mode="Markdown",
    )


async def cmd_stats(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the caller's win/loss stats."""
    user = update.effective_user
    _ensure_player(user)
    won, lost = db.get_player_stats(user.id)
    net = won - lost
    sign = "+" if net >= 0 else "−"
    await update.effective_message.reply_text(
        f"📊 {_md_name(user)}\n"
        f"Won: *${won}* · Lost: *${lost}*\n"
        f"Net: *{sign}${abs(net)}*",
        parse_mode="Markdown",
    )


# --- Admin/utility commands ---

async def cmd_settopic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run inside a group topic to set it as the casino location."""
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ Run this command inside a group topic.")
        return

    if not await _is_admin(update, context):
        logger.warning("uid=%d %s tried /settopic without admin rights in chat %d",
                       user.id, _display_name(user), chat.id)
        await msg.reply_text("❌ Only group admins can set the casino topic.")
        return

    topic_id = msg.message_thread_id
    if topic_id is None:
        await msg.reply_text("❌ Run this command inside a topic thread, not the main chat.")
        return

    db.set_casino_location(chat.id, topic_id)
    await msg.reply_text("✅ Casino topic set for this group!")
    logger.info("uid=%d %s set casino location to group=%d topic=%d",
                user.id, _display_name(user), chat.id, topic_id)


async def cmd_unsettopic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the casino topic so the bot stops responding in the group."""
    msg = update.effective_message
    user = update.effective_user

    if update.effective_chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ Run this command inside a group.")
        return

    if not await _is_admin(update, context):
        logger.warning("uid=%d %s tried /unsettopic without admin rights in chat %d",
                       user.id, _display_name(user), update.effective_chat.id)
        await msg.reply_text("❌ Only group admins can do this.")
        return

    db.clear_casino_location(update.effective_chat.id)
    await msg.reply_text("✅ Casino topic cleared for this group.")
    logger.info("uid=%d %s cleared casino location for group=%d",
                user.id, _display_name(user), update.effective_chat.id)


async def cmd_casino(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show casino configuration — per-group if in a group, all groups if in private."""
    chat = update.effective_chat

    if chat.type in ("group", "supergroup"):
        topic_id = db.get_casino_location(chat.id)
        if topic_id is None:
            await update.effective_message.reply_text(
                "🎰 No casino topic set for this group.\n"
                "An admin can run /settopic inside a topic to set it up."
            )
        else:
            await update.effective_message.reply_text(
                f"🎰 Casino is active in topic `{topic_id}`",
                parse_mode="Markdown",
            )
    else:
        locations = db.get_all_casino_locations()
        if not locations:
            await update.effective_message.reply_text(
                "🎰 No groups configured yet.\n"
                "An admin can run /settopic inside a group topic to set it up."
            )
        else:
            lines = "\n".join(f"• Group `{g}` — Topic `{t}`" for g, t in locations.items())
            await update.effective_message.reply_text(
                f"🎰 Active casino locations:\n{lines}",
                parse_mode="Markdown",
            )


async def cmd_casinostats(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show casino-wide win/loss totals."""
    paid_out, collected = db.get_casino_stats()
    net = collected - paid_out
    sign = "+" if net >= 0 else "−"
    await update.effective_message.reply_text(
        f"🏦 Casino stats\n"
        f"Paid out: *${paid_out}* · Collected: *${collected}*\n"
        f"Net: *{sign}${abs(net)}*",
        parse_mode="Markdown",
    )


# --- Admin commands ---

async def cmd_dodep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hidden admin command: /dodep @username amount  or  /dodep * amount."""
    user = update.effective_user
    msg = update.effective_message

    if (user.username or "").lower() != BOT_ADMIN.lower():
        return

    args = context.args
    reply = msg.reply_to_message

    # Resolve mode: reply, *, or @username
    if reply and len(args) == 1:
        # /dodep amount  (reply to target)
        amount_str = args[0]
        target_user = reply.from_user
        target_id = target_user.id
        safe_target = escape_markdown(_display_name(target_user))
        if db.get_balance(target_id) is None:
            await msg.reply_text(f"❌ {safe_target} not found.", parse_mode="Markdown")
            return
        try:
            amount = int(amount_str)
        except ValueError:
            await msg.reply_text("❌ Amount must be a whole number.")
            return
        new_balance = db.credit(target_id, amount)
        logger.info("Admin dodep: +$%d to uid=%d, new balance $%d", amount, target_id, new_balance)
        await msg.reply_text(
            f"✅ Dodep *+${amount}* → {safe_target} · 💰 *${new_balance}*",
            parse_mode="Markdown",
        )
    elif len(args) == 2 and args[0] == "*":
        try:
            amount = int(args[1])
        except ValueError:
            await msg.reply_text("❌ Amount must be a whole number.")
            return
        count = db.daily_deposit(amount)
        logger.info("Admin dodep: +$%d to all %d players", amount, count)
        await msg.reply_text(
            f"✅ Dodep *+${amount}* given to {count} players.",
            parse_mode="Markdown",
        )
    elif len(args) == 2:
        username = args[0].lstrip("@")
        try:
            amount = int(args[1])
        except ValueError:
            await msg.reply_text("❌ Amount must be a whole number.")
            return
        player = db.get_by_username(username)
        if player is None:
            await msg.reply_text(f"❌ Player {escape_markdown('@' + username)} not found.",
                                 parse_mode="Markdown")
            return
        new_balance = db.credit(player.user_id, amount)
        safe_target = escape_markdown(f"@{username}")
        logger.info("Admin dodep: +$%d to @%s (uid=%d), new balance $%d",
                    amount, username, player.user_id, new_balance)
        await msg.reply_text(
            f"✅ Dodep *+${amount}* → {safe_target} · 💰 *${new_balance}*",
            parse_mode="Markdown",
        )
    else:
        await msg.reply_text(
            "Usage: `/dodep @username amount`, `/dodep * amount`,"
            " or reply to a user with `/dodep amount`",
            parse_mode="Markdown",
        )


# --- App setup ---

async def _job_hourly_deposit(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Credit every player with the hourly deposit and announce it in all casino topics."""
    count = db.daily_deposit(HOURLY_DEPOSIT)
    logger.info("Hourly deposit: +$%d credited to %d players", HOURLY_DEPOSIT, count)
    if count:
        for group_id, topic_id in db.get_all_casino_locations().items():
            await context.bot.send_message(
                chat_id=group_id,
                message_thread_id=topic_id,
                text=f"🎁 Dodep: *+${HOURLY_DEPOSIT}* credited to all {count} players!",
                parse_mode="Markdown",
            )


async def _post_init(app) -> None:
    await app.bot.set_my_commands([
        ("balance",    "Check your current balance"),
        ("give",       "Send coins to another player"),
        ("stats",      "Show your win/loss statistics"),
        ("casinostats", "Show casino win/loss totals"),
        ("casino",      "Show where the casino is active"),
        ("settopic",   "Set this topic as the casino (admins only)"),
        ("unsettopic", "Remove the casino topic (admins only)"),
    ])
    now = datetime.now(timezone.utc)
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    app.job_queue.run_repeating(_job_hourly_deposit, interval=3600, first=next_hour)
    bot_info = await app.bot.get_me()
    logger.info("Bot ready: @%s (id=%d)", bot_info.username, bot_info.id)


def main() -> None:
    """Initialise the DB, register handlers, and start long-polling."""
    db.init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(_post_init).build()

    app.add_handler(MessageHandler(
        CasinoFilter() & filters.Dice.SLOT_MACHINE & ~filters.FORWARDED, handle_slot
    ))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("casinostats", cmd_casinostats))
    app.add_handler(CommandHandler("give", cmd_give))
    app.add_handler(CommandHandler("settopic", cmd_settopic))
    app.add_handler(CommandHandler("unsettopic", cmd_unsettopic))
    app.add_handler(CommandHandler("casino", cmd_casino))
    app.add_handler(CommandHandler("dodep", cmd_dodep))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
