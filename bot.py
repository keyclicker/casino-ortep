"""Telegram bot entry point — registers handlers and starts polling."""
import logging
import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import Update, Message
from telegram.helpers import escape_markdown
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

import db
from casino import HOURLY_DEPOSIT, TIER_BALANCE_CAP, calculate_score, get_spin_params

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
    """Markdown-safe display name (escapes _, *, `, [)."""
    return escape_markdown(_display_name(user))


def _ensure_player(user) -> int:
    """Register user if new and return their current balance."""
    return db.get_or_create(user.id, user.username or "")


def _is_bot_admin(user) -> bool:
    return (user.username or "").lower() == BOT_ADMIN.lower()


async def _is_chat_admin(update: Update, context) -> bool:
    member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    return member.status in ("administrator", "creator")


async def _reply(msg: Message, text: str, **kwargs) -> None:
    """Reply silently (no notification sound)."""
    await msg.reply_text(text, disable_notification=True, **kwargs)


# --- Slot handler ---

async def handle_slot(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle an incoming 🎰 dice message."""
    msg = update.effective_message
    user = update.effective_user
    name = _display_name(user)

    balance = _ensure_player(user)
    cost, win_mult = get_spin_params(balance)

    net, description = calculate_score(msg.dice.value, cost, win_mult)
    ok, new_balance = db.apply_spin(user.id, net, cost)
    if not ok:
        logger.warning("uid=%d %s tried to spin with insufficient funds ($%d)",
                       user.id, name, new_balance)
        await _reply(
            msg,
            f"❌ Not enough coins to spin!\n💰 *${new_balance}* · need *${cost}*",
            parse_mode="Markdown",
        )
        return

    payout = net + cost
    win_part = f" + *${payout}*" if payout > 0 else ""
    await _reply(
        msg,
        f"{description}\n💰 *-${cost}*{win_part} => *${new_balance}*",
        parse_mode="Markdown",
    )
    logger.info("uid=%d %s rolled 🎰 tier=%d cost=$%d payout=$%d net=$%+d | balance $%d",
                user.id, name, balance // TIER_BALANCE_CAP, cost, payout, net, new_balance)


# --- Player commands ---

async def cmd_balance(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the caller's current balance."""
    user = update.effective_user
    balance = _ensure_player(user)
    logger.info("uid=%d %s checked balance: $%d", user.id, _display_name(user), balance)
    await _reply(update.effective_message, f"💰 *${balance}*", parse_mode="Markdown")


async def cmd_give(  # pylint: disable=too-many-return-statements
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Transfer funds to another player: /give @username amount."""
    msg = update.effective_message
    user = update.effective_user
    _ensure_player(user)

    args = context.args
    reply = msg.reply_to_message

    if reply and len(args) == 1:
        target_user = reply.from_user
        target_id = target_user.id
        safe_target = escape_markdown(_display_name(target_user))
        if db.get_balance(target_id) is None:
            await _reply(msg,
                         f"❌ {safe_target} not found — they need to spin at least once first.",
                         parse_mode="Markdown")
            return
        amount_str = args[0]
    elif len(args) == 2:
        target_username = args[0].lstrip("@")
        target = db.get_by_username(target_username)
        if target is None:
            logger.warning("uid=%d %s tried to give to unknown player @%s",
                           user.id, _display_name(user), target_username)
            await _reply(msg,
                         f"❌ {escape_markdown('@' + target_username)} not found"
                         " — they need to spin at least once first.",
                         parse_mode="Markdown")
            return
        target_id = target.user_id
        safe_target = escape_markdown(f"@{target_username}")
        amount_str = args[1]
    else:
        await _reply(msg,
                     "Usage: `/give @username amount` or reply to a user with `/give amount`",
                     parse_mode="Markdown")
        return

    try:
        amount = int(amount_str)
    except ValueError:
        await _reply(msg, "❌ Amount must be a whole number.")
        return
    if amount <= 0:
        await _reply(msg, "❌ Amount must be positive.")
        return
    if target_id == user.id:
        await _reply(msg, "❌ You can't give money to yourself.")
        return

    try:
        from_bal, to_bal = db.transfer(user.id, target_id, amount)
    except ValueError:
        balance = db.get_balance(user.id)
        logger.warning("uid=%d %s failed to give $%d (insufficient funds, balance $%d)",
                       user.id, _display_name(user), amount, balance)
        await _reply(msg, f"❌ Not enough funds · 💰 *${balance}*", parse_mode="Markdown")
        return

    logger.info("uid=%d %s gave $%d to uid=%d | balances $%d → $%d",
                user.id, _display_name(user), amount, target_id, from_bal, to_bal)
    await _reply(
        msg,
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
    ratio = f"{won / lost:.2f}x" if lost else "N/A"
    await _reply(
        update.effective_message,
        f"📊 {_md_name(user)}\n"
        f"Won: *${won}* · Lost: *${lost}*\n"
        f"Net: *{sign}${abs(net)}* · Win ratio: *{ratio}*",
        parse_mode="Markdown",
    )


# --- Admin/utility commands ---

async def cmd_settopic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run inside a group topic to set it as the casino location."""
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        await _reply(msg, "❌ Run this command inside a group topic.")
        return
    if not await _is_chat_admin(update, context):
        logger.warning("uid=%d %s tried /settopic without admin rights in chat %d",
                       user.id, _display_name(user), chat.id)
        await _reply(msg, "❌ Only group admins can set the casino topic.")
        return
    if msg.message_thread_id is None:
        await _reply(msg, "❌ Run this command inside a topic thread, not the main chat.")
        return

    db.set_casino_location(chat.id, msg.message_thread_id)
    await _reply(msg, "✅ Casino topic set for this group!")
    logger.info("uid=%d %s set casino location to group=%d topic=%d",
                user.id, _display_name(user), chat.id, msg.message_thread_id)


async def cmd_unsettopic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the casino topic so the bot stops responding in the group."""
    msg = update.effective_message
    user = update.effective_user

    if update.effective_chat.type not in ("group", "supergroup"):
        await _reply(msg, "❌ Run this command inside a group.")
        return
    if not await _is_chat_admin(update, context):
        logger.warning("uid=%d %s tried /unsettopic without admin rights in chat %d",
                       user.id, _display_name(user), update.effective_chat.id)
        await _reply(msg, "❌ Only group admins can do this.")
        return

    db.clear_casino_location(update.effective_chat.id)
    await _reply(msg, "✅ Casino topic cleared for this group.")
    logger.info("uid=%d %s cleared casino location for group=%d",
                user.id, _display_name(user), update.effective_chat.id)


async def cmd_casino(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show casino configuration — per-group if in a group, all groups if in private."""
    chat = update.effective_chat
    msg = update.effective_message

    if chat.type in ("group", "supergroup"):
        topic_id = db.get_casino_location(chat.id)
        if topic_id is None:
            await _reply(msg, "🎰 No casino topic set for this group.\n"
                             "An admin can run /settopic inside a topic to set it up.")
        else:
            await _reply(msg, f"🎰 Casino is active in topic `{topic_id}`",
                         parse_mode="Markdown")
    else:
        locations = db.get_all_casino_locations()
        if not locations:
            await _reply(msg, "🎰 No groups configured yet.\n"
                             "An admin can run /settopic inside a group topic to set it up.")
        else:
            lines = "\n".join(f"• Group `{g}` — Topic `{t}`" for g, t in locations.items())
            await _reply(msg, f"🎰 Active casino locations:\n{lines}", parse_mode="Markdown")


async def cmd_casinostats(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show casino-wide win/loss totals."""
    paid_out, collected = db.get_casino_stats()
    net = collected - paid_out
    sign = "+" if net >= 0 else "−"
    ratio = f"{collected / paid_out:.2f}x" if paid_out else "N/A"
    await _reply(
        update.effective_message,
        f"🏦 Casino stats\n"
        f"Paid out: *${paid_out}* · Collected: *${collected}*\n"
        f"Net: *{sign}${abs(net)}* · Win ratio: *{ratio}*",
        parse_mode="Markdown",
    )


# --- Admin commands ---

async def cmd_dodep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hidden admin command: /dodep @username amount  or  /dodep * amount."""
    user = update.effective_user
    msg = update.effective_message
    if not _is_bot_admin(user):
        return

    args = context.args
    reply = msg.reply_to_message
    broadcast = False

    # Resolve target
    if reply and len(args) == 1:
        target_user = reply.from_user
        if db.get_balance(target_user.id) is None:
            await _reply(msg, f"❌ {escape_markdown(_display_name(target_user))} not found.",
                         parse_mode="Markdown")
            return
        target_id = target_user.id
        safe_target = escape_markdown(_display_name(target_user))
        amount_str = args[0]
    elif len(args) == 2 and args[0] == "*":
        broadcast = True
        amount_str = args[1]
    elif len(args) == 2:
        target_username = args[0].lstrip("@")
        player = db.get_by_username(target_username)
        if player is None:
            await _reply(msg, f"❌ Player {escape_markdown('@' + target_username)} not found.",
                         parse_mode="Markdown")
            return
        target_id = player.user_id
        safe_target = escape_markdown(f"@{target_username}")
        amount_str = args[1]
    else:
        await _reply(msg,
                     "Usage: `/dodep @username amount`, `/dodep * amount`,"
                     " or reply to a user with `/dodep amount`",
                     parse_mode="Markdown")
        return

    try:
        amount = int(amount_str)
    except ValueError:
        await _reply(msg, "❌ Amount must be a whole number.")
        return

    if broadcast:
        count = db.daily_deposit(amount)
        logger.info("Admin dodep: +$%d to all %d players", amount, count)
        await _reply(msg, f"✅ Dodep *+${amount}* given to {count} players.",
                     parse_mode="Markdown")
    else:
        new_balance = db.credit(target_id, amount)
        logger.info("Admin dodep: +$%d to uid=%d, new balance $%d", amount, target_id, new_balance)
        await _reply(msg, f"✅ Dodep *+${amount}* → {safe_target} · 💰 *${new_balance}*",
                     parse_mode="Markdown")


async def cmd_balances(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hidden admin command: list all players sorted by balance descending."""
    if not _is_bot_admin(update.effective_user):
        return

    players = db.get_all_players_by_balance()
    if not players:
        await _reply(update.effective_message, "No players yet.")
        return

    lines = [
        f"{i}. {escape_markdown(p.username) if p.username else str(p.user_id)} — *${p.balance}*"
        for i, p in enumerate(players, 1)
    ]
    await _reply(
        update.effective_message,
        f"👥 *Players ({len(players)})*\n" + "\n".join(lines),
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
                disable_notification=True,
            )


async def _post_init(app) -> None:
    await app.bot.set_my_commands([
        ("balance",     "Check your current balance"),
        ("give",        "Send coins to another player"),
        ("stats",       "Show your win/loss statistics"),
        ("casinostats", "Show casino win/loss totals"),
        ("casino",      "Show where the casino is active"),
        ("settopic",    "Set this topic as the casino (admins only)"),
        ("unsettopic",  "Remove the casino topic (admins only)"),
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
    app.add_handler(CommandHandler("balance",     cmd_balance))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("casinostats", cmd_casinostats))
    app.add_handler(CommandHandler("give",        cmd_give))
    app.add_handler(CommandHandler("settopic",    cmd_settopic))
    app.add_handler(CommandHandler("unsettopic",  cmd_unsettopic))
    app.add_handler(CommandHandler("casino",      cmd_casino))
    app.add_handler(CommandHandler("dodep",       cmd_dodep))
    app.add_handler(CommandHandler("balances",    cmd_balances))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
