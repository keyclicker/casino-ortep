"""Telegram command and message handlers."""
import html
import logging
import time

from telegram import MessageEntity, Update
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes

import db
from casino import (
    LOSS_CAP_FRAC, SPIN_WINDOW,
    base_unit, calculate_score, compute_cdm,
)
from helpers import (
    SPOILER_DELAY,
    display_name, md_name, ensure_player, note_membership,
    is_bot_admin, is_chat_admin, reply,
)
from jobs import reveal_message

logger = logging.getLogger(__name__)


# ── Slot machine ───────────────────────────────────────────────────────────

async def handle_slot(  # pylint: disable=too-many-locals
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle an incoming 🎰 dice message."""
    msg  = update.effective_message
    user = update.effective_user
    name = display_name(user)

    balance = ensure_player(user)
    note_membership(update)  # remember this player plays in this group

    # cdm — "casino dynamic magic" — reacts to the player's own history: it eases
    # up (cdm>1) when they're losing and tightens (cdm<1) when they're winning.
    recent_net, life_net, life_staked = db.get_spin_signals(user.id, SPIN_WINDOW)
    base = base_unit(balance)
    cdm = compute_cdm(recent_net, base, life_net, life_staked)

    net, cost, description = calculate_score(msg.dice.value, base, cdm)
    # Per-spin loss cap + can't lose more than the current balance.
    net = max(net, -round(LOSS_CAP_FRAC * balance), -balance)

    ok, new_balance = db.apply_spin(user.id, net, cost, outcome=description)
    if not ok:
        logger.warning(
            "uid=%d %s tried to spin with insufficient funds ($%d)",
            user.id, name, new_balance,
        )
        await reply(
            msg,
            f"❌ Not enough coins to spin!\n💰 *${new_balance}* · need *${cost}*",
            parse_mode="Markdown",
        )
        return

    # "Hot machine" cue: when cdm is generous, advertise it to amplify the
    # comeback dopamine. When it's tightening (cdm<1), stay quiet.
    flair = "🔥 " if cdm >= 1.10 else ""

    payout = net + cost
    if payout > 0:
        balance_line       = f"{flair}💰 *-${cost}* + *${payout}* => *${new_balance}*"
        balance_line_plain = f"{flair}💰 -${cost} + ${payout} => ${new_balance}"
    else:
        balance_line       = f"{flair}💰 *-${-net}* => *${new_balance}*"
        balance_line_plain = f"{flair}💰 -${-net} => ${new_balance}"

    reveal_text  = f"{description}\n{balance_line}"
    spoiler_text = f"{description}\n{balance_line_plain}"

    # Send the result hidden under a spoiler so players can choose when to reveal.
    # The length must be in UTF-16 code units (Telegram's wire format).
    utf16_len = len(spoiler_text.encode("utf-16-le")) // 2
    sent = await msg.reply_text(
        spoiler_text,
        entities=[MessageEntity(type=MessageEntity.SPOILER, offset=0, length=utf16_len)],
        disable_notification=True,
    )

    # Persist before scheduling — ensures the reveal survives a restart.
    reveal_at = time.time() + SPOILER_DELAY
    reveal_id = db.add_pending_reveal(sent.chat_id, sent.message_id, reveal_text, reveal_at)
    context.application.job_queue.run_once(
        reveal_message,
        when=SPOILER_DELAY,
        data=(sent.chat_id, sent.message_id, reveal_text, reveal_id),
    )

    logger.info(
        "uid=%d %s rolled 🎰 cdm=%.2f base=$%d cost=$%d payout=$%d net=$%+d | balance $%d",
        user.id, name, cdm, base, cost, payout, net, new_balance,
    )


# ── Player commands ────────────────────────────────────────────────────────

async def cmd_give(  # pylint: disable=too-many-locals,too-many-return-statements
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Transfer funds to another player: /give @username amount."""
    msg  = update.effective_message
    user = update.effective_user
    ensure_player(user)
    note_membership(update)

    args     = context.args
    reply_to = msg.reply_to_message

    # Resolve target from either a reply-to message or an explicit @username arg.
    if reply_to and len(args) == 1:
        target_user = reply_to.from_user
        target_id   = target_user.id
        safe_target = escape_markdown(display_name(target_user))
        if db.get_balance(target_id) is None:
            await reply(
                msg,
                f"❌ {safe_target} not found — they need to spin at least once first.",
                parse_mode="Markdown",
            )
            return
        amount_str = args[0]
    elif len(args) == 2:
        target_username = args[0].lstrip("@")
        target = db.get_by_username(target_username)
        if target is None:
            logger.warning(
                "uid=%d %s tried to give to unknown player @%s",
                user.id, display_name(user), target_username,
            )
            await reply(
                msg,
                f"❌ {escape_markdown('@' + target_username)} not found"
                " — they need to spin at least once first.",
                parse_mode="Markdown",
            )
            return
        target_id   = target.user_id
        safe_target = escape_markdown(f"@{target_username}")
        amount_str  = args[1]
    else:
        await reply(
            msg,
            "Usage: `/give @username amount` or reply to a user with `/give amount`",
            parse_mode="Markdown",
        )
        return

    try:
        amount = int(amount_str)
    except ValueError:
        await reply(msg, "❌ Amount must be a whole number.")
        return
    if amount <= 0:
        await reply(msg, "❌ Amount must be positive.")
        return
    if target_id == user.id:
        await reply(msg, "❌ You can't give money to yourself.")
        return

    try:
        from_bal, to_bal = db.transfer(user.id, target_id, amount)
    except ValueError:
        balance = db.get_balance(user.id)
        logger.warning(
            "uid=%d %s failed to give $%d (insufficient funds, balance $%d)",
            user.id, display_name(user), amount, balance,
        )
        await reply(msg, f"❌ Not enough funds · 💰 *${balance}*", parse_mode="Markdown")
        return

    logger.info(
        "uid=%d %s gave $%d to uid=%d | balances $%d → $%d",
        user.id, display_name(user), amount, target_id, from_bal, to_bal,
    )
    await reply(
        msg,
        f"✅ {md_name(user)} → {safe_target} · *${amount}*\n"
        f"💰 You: *${from_bal}* · {safe_target}: *${to_bal}*",
        parse_mode="Markdown",
    )


async def cmd_stats(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the caller's balance and win/loss stats."""
    user    = update.effective_user
    balance = ensure_player(user)
    note_membership(update)
    won, lost = db.get_player_stats(user.id)
    net   = won - lost
    sign  = "+" if net >= 0 else "−"
    ratio = f"{won / lost:.2f}x" if lost else "N/A"

    best, streak_len, streak_win = db.get_player_highlights(user.id)
    if streak_len == 0:
        streak = "—"
    else:
        streak = f"🔥 {streak_len}W" if streak_win else f"❄️ {streak_len}L"

    logger.info("uid=%d %s checked stats: balance=$%d won=$%d lost=$%d",
                user.id, display_name(user), balance, won, lost)
    await reply(
        update.effective_message,
        f"📊 {md_name(user)}\n"
        f"Balance: *${balance}*\n"
        f"Won: *${won}* · Lost: *${lost}*\n"
        f"Net: *{sign}${abs(net)}* · Win ratio: *{ratio}*\n"
        f"Best win: *${best}* · Streak: *{streak}*",
        parse_mode="Markdown",
    )


# Per-ledger-kind icon for the /history feed.
_KIND_ICON = {
    db.LedgerKind.SIGNUP:       "🎁",
    db.LedgerKind.SPIN_COST:    "🎰",
    db.LedgerKind.SPIN_WIN:     "💰",
    db.LedgerKind.SPIN_PENALTY: "💥",
    db.LedgerKind.GIVE_OUT:     "📤",
    db.LedgerKind.GIVE_IN:      "📥",
    db.LedgerKind.DODEP_ADMIN:  "🛠",
    db.LedgerKind.DODEP_CASINO: "⏰",
}


async def cmd_history(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the caller's recent transactions from the ledger."""
    user = update.effective_user
    ensure_player(user)
    note_membership(update)

    entries = db.get_ledger(user.id, limit=12)
    if not entries:
        await reply(update.effective_message, "🧾 No activity yet — spin to get started!")
        return

    def _memo(e) -> str:
        # /give rows store the counterparty id ("to:42"/"from:42") — show the @name.
        if e.kind in (db.LedgerKind.GIVE_OUT, db.LedgerKind.GIVE_IN):
            _, _, raw = e.memo.partition(":")
            try:
                other = int(raw)
            except ValueError:
                return e.memo
            name = db.get_username(other)
            who = f"@{name}" if name else f"#{other}"
            arrow = "→" if e.kind == db.LedgerKind.GIVE_OUT else "←"
            return f"{arrow} {who}"
        return e.memo

    # HTML (not Markdown): memos carry arbitrary outcome text + usernames, which
    # routinely break Telegram's fragile legacy Markdown parser.
    def _line(e) -> str:
        icon = _KIND_ICON.get(e.kind, "•")
        amount = f"+${e.amount}" if e.amount >= 0 else f"−${-e.amount}"
        memo = _memo(e)
        memo_part = f" <i>{html.escape(memo)}</i>" if memo else ""
        return f"{icon} <b>{amount}</b>{memo_part}"

    lines = "\n".join(_line(e) for e in entries)
    await reply(
        update.effective_message,
        f"🧾 <b>Recent activity</b>\n{lines}",
        parse_mode="HTML",
    )


# ── Group-admin commands ───────────────────────────────────────────────────

async def cmd_settopic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the current topic as the casino location for this group."""
    msg  = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        await reply(msg, "❌ Run this command inside a group topic.")
        return
    if not await is_chat_admin(update, context):
        logger.warning(
            "uid=%d %s tried /settopic without admin rights in chat %d",
            user.id, display_name(user), chat.id,
        )
        await reply(msg, "❌ Only group admins can set the casino topic.")
        return
    if msg.message_thread_id is None:
        await reply(msg, "❌ Run this command inside a topic thread, not the main chat.")
        return

    db.set_casino_location(chat.id, msg.message_thread_id)
    logger.info(
        "uid=%d %s set casino location to group=%d topic=%d",
        user.id, display_name(user), chat.id, msg.message_thread_id,
    )
    await reply(msg, "✅ Casino topic set for this group!")


async def cmd_unsettopic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the casino topic so the bot stops responding in this group."""
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if chat.type not in ("group", "supergroup"):
        await reply(msg, "❌ Run this command inside a group.")
        return
    if not await is_chat_admin(update, context):
        logger.warning(
            "uid=%d %s tried /unsettopic without admin rights in chat %d",
            user.id, display_name(user), chat.id,
        )
        await reply(msg, "❌ Only group admins can do this.")
        return

    db.clear_casino_location(chat.id)
    logger.info(
        "uid=%d %s cleared casino location for group=%d",
        user.id, display_name(user), chat.id,
    )
    await reply(msg, "✅ Casino topic cleared for this group.")


async def cmd_casino(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show casino configuration — per-group inside a group, all groups in private."""
    chat = update.effective_chat
    msg  = update.effective_message

    if chat.type in ("group", "supergroup"):
        topic_id = db.get_casino_location(chat.id)
        if topic_id is None:
            await reply(
                msg,
                "🎰 No casino topic set for this group.\n"
                "An admin can run /settopic inside a topic to set it up.",
            )
        else:
            await reply(msg, f"🎰 Casino is active in topic `{topic_id}`",
                        parse_mode="Markdown")
    else:
        locations = db.get_all_casino_locations()
        if not locations:
            await reply(
                msg,
                "🎰 No groups configured yet.\n"
                "An admin can run /settopic inside a group topic to set it up.",
            )
        else:
            lines = "\n".join(
                f"• Group `{g}` — Topic `{t}`" for g, t in locations.items()
            )
            await reply(msg, f"🎰 Active casino locations:\n{lines}", parse_mode="Markdown")


async def cmd_casinostats(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show casino-wide win/loss totals."""
    paid_out, collected, total_balance = db.get_casino_stats()
    net   = collected - paid_out
    sign  = "+" if net >= 0 else "−"
    ratio = f"{collected / paid_out:.2f}x" if paid_out else "N/A"
    await reply(
        update.effective_message,
        f"🏦 Casino stats\n"
        f"Paid out: *${paid_out}* · Collected: *${collected}*\n"
        f"Net: *{sign}${abs(net)}* · Win ratio: *{ratio}*\n"
        f"Players cap: *${total_balance}*",
        parse_mode="Markdown",
    )


# ── Bot-admin commands ─────────────────────────────────────────────────────

async def cmd_dodep(  # pylint: disable=too-many-locals
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """[Admin] Deposit funds to a player or to all players.

    Usage:
      /dodep @username amount  — credit a specific player
      /dodep * amount          — credit every registered player
      reply to a message + /dodep amount — credit the replied-to user
    """
    user = update.effective_user
    msg  = update.effective_message
    if not is_bot_admin(user):
        return

    args      = context.args
    reply_to  = msg.reply_to_message
    broadcast = False

    # Initialised here so pylint knows they're always bound before use below.
    target_id:   int = 0
    safe_target: str = ""

    if reply_to and len(args) == 1:
        target_user = reply_to.from_user
        if db.get_balance(target_user.id) is None:
            await reply(
                msg,
                f"❌ {escape_markdown(display_name(target_user))} not found.",
                parse_mode="Markdown",
            )
            return
        target_id   = target_user.id
        safe_target = escape_markdown(display_name(target_user))
        amount_str  = args[0]
    elif len(args) == 2 and args[0] == "*":
        broadcast  = True
        amount_str = args[1]
    elif len(args) == 2:
        target_username = args[0].lstrip("@")
        player = db.get_by_username(target_username)
        if player is None:
            await reply(
                msg,
                f"❌ Player {escape_markdown('@' + target_username)} not found.",
                parse_mode="Markdown",
            )
            return
        target_id   = player.user_id
        safe_target = escape_markdown(f"@{target_username}")
        amount_str  = args[1]
    else:
        await reply(
            msg,
            "Usage: `/dodep @username amount`, `/dodep * amount`,"
            " or reply to a user with `/dodep amount`",
            parse_mode="Markdown",
        )
        return

    try:
        amount = int(amount_str)
    except ValueError:
        await reply(msg, "❌ Amount must be a whole number.")
        return

    if broadcast:
        count = db.daily_deposit(amount)
        logger.info("Admin dodep: +$%d to all %d players", amount, count)
        await reply(
            msg,
            f"✅ Dodep *+${amount}* given to {count} players.",
            parse_mode="Markdown",
        )
    else:
        new_balance = db.credit(target_id, amount)
        logger.info("Admin dodep: +$%d to uid=%d, new balance $%d", amount, target_id, new_balance)
        await reply(
            msg,
            f"✅ Dodep *+${amount}* → {safe_target} · 💰 *${new_balance}*",
            parse_mode="Markdown",
        )


def _format_leaderboard(rows: list, title: str) -> str:
    """Render leaderboard rows (player, won, lost) as a Markdown list."""
    def _ratio(won: int, lost: int) -> str:
        return f"{won / lost:.2f}x" if lost else "N/A"

    lines = [
        f"{i}. {escape_markdown(p.username) if p.username else str(p.user_id)}"
        f" — *${p.balance}* _({_ratio(won, lost)})_"
        for i, (p, won, lost) in enumerate(rows, 1)
    ]
    return f"👥 *{title} ({len(lines)})*\n" + "\n".join(lines)


async def cmd_balances(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Leaderboard of the players active in the current group."""
    msg = update.effective_message
    group_id = note_membership(update)  # also records the caller as a member
    if group_id is None:
        await reply(msg, "🎰 Use /balances inside a casino group to see its leaderboard.")
        return

    rows = db.get_leaderboard(group_id)
    if not rows:
        await reply(msg, "No players here yet.")
        return
    await reply(msg, _format_leaderboard(rows, "Players here"), parse_mode="Markdown")


async def cmd_all_balances(  # pylint: disable=unused-argument
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """[Admin] Global leaderboard across every player."""
    if not is_bot_admin(update.effective_user):
        return
    rows = db.get_leaderboard()
    if not rows:
        await reply(update.effective_message, "No players yet.")
        return
    await reply(
        update.effective_message,
        _format_leaderboard(rows, "All players"),
        parse_mode="Markdown",
    )
