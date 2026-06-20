"""Database layer — SQLAlchemy models and query helpers for the casino bot.

The `ledger` table is the single source of truth for the economy. *Every* event
that moves a balance — signup grants, spin prices, spin wins, spin penalties,
/give transfers, admin dodeps, hourly casino dodeps — is an immutable signed
`Ledger` row. A player's balance is exactly the sum of their ledger amounts, and
all stats (won/lost/net, casino totals, cdm signals) are derived from it.

`Player.balance` is a materialized cache of that sum, updated in the same
transaction as every ledger insert, so reads stay cheap; `recompute_balance()`
rebuilds it from the ledger to prove (or repair) consistency.
"""
import time
from pathlib import Path
from sqlalchemy import create_engine, select, delete, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from alembic.config import Config
from alembic import command

DB_URL = "sqlite:///casino.db"
DEFAULT_BALANCE = 100

engine = create_engine(DB_URL)


class LedgerKind:  # pylint: disable=too-few-public-methods
    """Every kind of balance-moving event recorded in the ledger."""
    SIGNUP = "signup"              # initial DEFAULT_BALANCE grant
    SPIN_COST = "spin_cost"        # price paid to spin (negative)
    SPIN_WIN = "spin_win"          # payout on a winning spin (positive)
    SPIN_PENALTY = "spin_penalty"  # extra loss on a BAR penalty (negative)
    GIVE_OUT = "give_out"          # /give sent (negative)
    GIVE_IN = "give_in"            # /give received (positive)
    DODEP_ADMIN = "dodep_admin"    # bot-admin credit (positive)
    DODEP_CASINO = "dodep_casino"  # hourly casino deposit (positive)


# The gambling-only kinds — the basis for win/loss stats and cdm signals.
SPIN_KINDS = (LedgerKind.SPIN_COST, LedgerKind.SPIN_WIN, LedgerKind.SPIN_PENALTY)
SPENT_KINDS = (LedgerKind.SPIN_COST, LedgerKind.SPIN_PENALTY)


class Base(DeclarativeBase):  # pylint: disable=too-few-public-methods
    """Declarative base for all ORM models."""


class Player(Base):  # pylint: disable=too-few-public-methods
    """A registered bot user. `balance` is a cache of the player's ledger sum."""
    __tablename__ = "players"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(default="")
    balance: Mapped[int] = mapped_column(default=0)


class Ledger(Base):  # pylint: disable=too-few-public-methods
    """One immutable balance-moving event. Source of truth for the economy."""
    __tablename__ = "ledger"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(index=True)
    amount: Mapped[int]                               # signed delta to the balance
    kind: Mapped[str]                                 # one of LedgerKind
    memo: Mapped[str] = mapped_column(default="")     # outcome text / counterparty
    created_at: Mapped[float]                         # Unix timestamp (UTC)


class CasinoLocation(Base):  # pylint: disable=too-few-public-methods
    """A group (and its topic) where the casino is active."""
    __tablename__ = "casino_locations"

    group_id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int]


class PlayerGroup(Base):  # pylint: disable=too-few-public-methods
    """Many-to-many: a player is known to play in a group (one row per pair)."""
    __tablename__ = "player_groups"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(primary_key=True, index=True)
    first_seen: Mapped[float]
    last_seen: Mapped[float]


class PendingReveal(Base):  # pylint: disable=too-few-public-methods
    """A slot result message waiting to have its spoiler lifted."""
    __tablename__ = "pending_reveals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int]
    message_id: Mapped[int]
    reveal_text: Mapped[str]
    reveal_at: Mapped[float]  # Unix timestamp (UTC)


def init_db() -> None:
    """Run all pending Alembic migrations."""
    cfg = Config(Path(__file__).parent / "alembic.ini")
    command.upgrade(cfg, "head")


def _post(s: Session, player: Player, amount: int, kind: str,
          memo: str = "", created_at: float | None = None) -> None:
    """Append a ledger row and update the player's cached balance, in `s`."""
    player.balance += amount
    s.add(Ledger(
        user_id=player.user_id, amount=amount, kind=kind, memo=memo,
        created_at=created_at if created_at is not None else time.time(),
    ))


# --- Players ---

def get_or_create(user_id: int, username: str) -> int:
    """Return the player's balance, creating them with DEFAULT_BALANCE if new."""
    with Session(engine) as s:
        player = s.get(Player, user_id)
        if player is None:
            player = Player(user_id=user_id, username=username.lower(), balance=0)
            s.add(player)
            _post(s, player, DEFAULT_BALANCE, LedgerKind.SIGNUP, "opening balance")
        else:
            player.username = username.lower()
        s.commit()
        return player.balance


def get_balance(user_id: int) -> int | None:
    """Return the player's balance, or None if they don't exist."""
    with Session(engine) as s:
        player = s.get(Player, user_id)
        return player.balance if player else None


def recompute_balance(user_id: int) -> int | None:
    """Rebuild a player's balance from the ledger. Returns the recomputed balance."""
    with Session(engine) as s:
        player = s.get(Player, user_id)
        if player is None:
            return None
        total = s.scalar(
            select(func.sum(Ledger.amount)).where(Ledger.user_id == user_id)
        ) or 0
        player.balance = total
        s.commit()
        return total


def apply_spin(
    user_id: int, net: int, cost: int,
    outcome: str = "", created_at: float | None = None,
) -> tuple[bool, int]:
    """Atomically check the player can afford the spin and apply it.

    Records the spin as ledger rows that decompose `net`: a SPIN_COST debit of
    `cost`, plus a SPIN_WIN credit or SPIN_PENALTY debit for the remainder (none
    on a break-even spin). Returns (ok, new_balance); ok=False = insufficient funds.
    """
    with Session(engine) as s:
        player = s.get(Player, user_id)
        if player is None or player.balance < cost:
            return False, (player.balance if player else 0)
        ts = created_at if created_at is not None else time.time()
        _post(s, player, -cost, LedgerKind.SPIN_COST, outcome, ts)
        result = net + cost  # payout (>0), penalty (<0), or break-even (0)
        if result > 0:
            _post(s, player, result, LedgerKind.SPIN_WIN, created_at=ts)
        elif result < 0:
            _post(s, player, result, LedgerKind.SPIN_PENALTY, created_at=ts)
        s.commit()
        return True, player.balance


def get_player_stats(user_id: int) -> tuple[int, int] | None:
    """Return (won, lost) for a player from the ledger, or None if not found.

    `won` = gross spin payouts; `lost` = gross spent on spins (prices + penalties).
    """
    with Session(engine) as s:
        if s.get(Player, user_id) is None:
            return None
        won = s.scalar(select(func.sum(Ledger.amount)).where(
            Ledger.user_id == user_id, Ledger.kind == LedgerKind.SPIN_WIN)) or 0
        spent = s.scalar(select(func.sum(Ledger.amount)).where(
            Ledger.user_id == user_id, Ledger.kind.in_(SPENT_KINDS))) or 0
        return won, -spent


def get_spin_signals(user_id: int, window: int) -> tuple[int, int, int]:
    """Return (recent_net, life_net, life_staked) — the inputs to compute_cdm.

    `recent_net` is the net of the player's last `window` spins; `life_net` is
    their lifetime gambling net; `life_staked` is the total ever paid in prices.
    All derived from the ledger's spin rows. Zeros for a player with no spins.
    """
    with Session(engine) as s:
        # id of the window-th most recent spin: sum every spin row at or after it.
        threshold = s.scalar(
            select(Ledger.id)
            .where(Ledger.user_id == user_id, Ledger.kind == LedgerKind.SPIN_COST)
            .order_by(Ledger.id.desc())
            .limit(1).offset(window - 1)
        )
        recent_q = select(func.sum(Ledger.amount)).where(
            Ledger.user_id == user_id, Ledger.kind.in_(SPIN_KINDS))
        if threshold is not None:
            recent_q = recent_q.where(Ledger.id >= threshold)
        recent_net = s.scalar(recent_q) or 0

        life_net = s.scalar(select(func.sum(Ledger.amount)).where(
            Ledger.user_id == user_id, Ledger.kind.in_(SPIN_KINDS))) or 0
        staked = s.scalar(select(func.sum(Ledger.amount)).where(
            Ledger.user_id == user_id, Ledger.kind == LedgerKind.SPIN_COST)) or 0
        return recent_net, life_net, -staked


def get_ledger(user_id: int, limit: int = 20) -> list[Ledger]:
    """Return a player's most recent ledger entries, newest first."""
    with Session(engine) as s:
        return list(s.scalars(
            select(Ledger)
            .where(Ledger.user_id == user_id)
            .order_by(Ledger.id.desc())
            .limit(limit)
        ).all())


def _recent_spin_nets(s: Session, user_id: int, max_spins: int) -> list[int]:
    """Per-spin net for the most recent spins, newest first (rebuilt from rows).

    A spin is one SPIN_COST row plus its optional SPIN_WIN/SPIN_PENALTY row; in
    id-descending order the result row precedes its cost row, so we accumulate
    until a SPIN_COST closes the spin.
    """
    rows = s.execute(
        select(Ledger.kind, Ledger.amount)
        .where(Ledger.user_id == user_id, Ledger.kind.in_(SPIN_KINDS))
        .order_by(Ledger.id.desc())
        .limit(max_spins * 2)
    ).all()
    nets: list[int] = []
    cur = 0
    for kind, amount in rows:
        cur += amount
        if kind == LedgerKind.SPIN_COST:
            nets.append(cur)
            cur = 0
            if len(nets) >= max_spins:
                break
    return nets


def get_player_highlights(user_id: int) -> tuple[int, int, bool]:
    """Return (biggest_win, streak_len, streak_is_win) from the ledger.

    `biggest_win` is the largest single SPIN_WIN payout; the streak counts the
    most recent consecutive spins of the same direction (win = net > 0).
    """
    with Session(engine) as s:
        biggest = s.scalar(select(func.max(Ledger.amount)).where(
            Ledger.user_id == user_id, Ledger.kind == LedgerKind.SPIN_WIN)) or 0
        nets = _recent_spin_nets(s, user_id, max_spins=100)
        if not nets:
            return biggest, 0, False
        is_win = nets[0] > 0
        length = 0
        for net in nets:
            if (net > 0) != is_win:
                break
            length += 1
        return biggest, length, is_win


def get_casino_stats() -> tuple[int, int, int]:
    """Return (total_paid_out, total_collected, total_balance) from the ledger."""
    with Session(engine) as s:
        paid_out = s.scalar(select(func.sum(Ledger.amount)).where(
            Ledger.kind == LedgerKind.SPIN_WIN)) or 0
        spent = s.scalar(select(func.sum(Ledger.amount)).where(
            Ledger.kind.in_(SPENT_KINDS))) or 0
        total_balance = s.scalar(select(func.sum(Player.balance))) or 0
        return paid_out, -spent, total_balance


def transfer(from_id: int, to_id: int, amount: int) -> tuple[int, int]:
    """Transfer amount from sender to receiver. Raises ValueError on bad state."""
    with Session(engine) as s:
        sender = s.get(Player, from_id)
        if sender is None or sender.balance < amount:
            raise ValueError("insufficient_funds")
        receiver = s.get(Player, to_id)
        if receiver is None:
            raise ValueError("receiver_not_found")
        ts = time.time()
        _post(s, sender, -amount, LedgerKind.GIVE_OUT, f"to:{to_id}", ts)
        _post(s, receiver, amount, LedgerKind.GIVE_IN, f"from:{from_id}", ts)
        s.commit()
        return sender.balance, receiver.balance


def get_username(user_id: int) -> str:
    """Return a player's username, or '' if unknown."""
    with Session(engine) as s:
        player = s.get(Player, user_id)
        return player.username if player else ""


def get_by_username(username: str) -> Player | None:
    """Look up a player by username (leading @ stripped)."""
    with Session(engine) as s:
        needle = username.lstrip("@").lower()
        return s.scalars(
            select(Player).where(func.lower(Player.username) == needle)
        ).first()


def credit(user_id: int, amount: int) -> int:
    """Bot-admin dodep: credit a specific player. Returns new balance."""
    with Session(engine) as s:
        player = s.get(Player, user_id)
        if player is None:
            raise ValueError("player_not_found")
        _post(s, player, amount, LedgerKind.DODEP_ADMIN, "admin dodep")
        s.commit()
        return player.balance


def get_leaderboard(group_id: int | None = None) -> list[tuple[Player, int, int]]:
    """Return (player, won, lost) sorted by balance descending.

    When `group_id` is given, only players known to play in that group are listed
    (see `record_membership`); otherwise every player is returned. `won`/`lost`
    are gross spin payouts / gross spent, computed from the ledger.
    """
    with Session(engine) as s:
        q = select(Player)
        if group_id is not None:
            members = select(PlayerGroup.user_id).where(PlayerGroup.group_id == group_id)
            q = q.where(Player.user_id.in_(members))
        players = list(s.scalars(q.order_by(Player.balance.desc())).all())
        won_map = dict(s.execute(
            select(Ledger.user_id, func.sum(Ledger.amount))
            .where(Ledger.kind == LedgerKind.SPIN_WIN)
            .group_by(Ledger.user_id)
        ).all())
        spent_map = dict(s.execute(
            select(Ledger.user_id, func.sum(Ledger.amount))
            .where(Ledger.kind.in_(SPENT_KINDS))
            .group_by(Ledger.user_id)
        ).all())
        return [
            (p, won_map.get(p.user_id, 0) or 0, -(spent_map.get(p.user_id, 0) or 0))
            for p in players
        ]


def daily_deposit(amount: int) -> int:
    """Casino dodep: credit every registered player. Returns the player count."""
    with Session(engine) as s:
        players = list(s.scalars(select(Player)).all())
        ts = time.time()
        for player in players:
            _post(s, player, amount, LedgerKind.DODEP_CASINO, "hourly deposit", ts)
        s.commit()
        return len(players)


# --- Player ↔ group membership ---

def record_membership(user_id: int, group_id: int, ts: float | None = None) -> None:
    """Remember that a player plays in a group (idempotent; refreshes last_seen)."""
    when = ts if ts is not None else time.time()
    with Session(engine) as s:
        row = s.get(PlayerGroup, (user_id, group_id))
        if row is None:
            s.add(PlayerGroup(user_id=user_id, group_id=group_id,
                              first_seen=when, last_seen=when))
        else:
            row.last_seen = when
        s.commit()


def get_user_groups(user_id: int) -> list[int]:
    """Return the group ids a player is known to play in."""
    with Session(engine) as s:
        return list(s.scalars(
            select(PlayerGroup.group_id).where(PlayerGroup.user_id == user_id)
        ).all())


def get_group_members(group_id: int) -> list[int]:
    """Return the user ids known to play in a group."""
    with Session(engine) as s:
        return list(s.scalars(
            select(PlayerGroup.user_id).where(PlayerGroup.group_id == group_id)
        ).all())


# --- Casino locations ---

def set_casino_location(group_id: int, topic_id: int) -> None:
    """Register or update the topic for a group."""
    with Session(engine) as s:
        row = s.get(CasinoLocation, group_id)
        if row is None:
            s.add(CasinoLocation(group_id=group_id, topic_id=topic_id))
        else:
            row.topic_id = topic_id
        s.commit()


def clear_casino_location(group_id: int) -> None:
    """Remove a group's casino registration."""
    with Session(engine) as s:
        s.execute(delete(CasinoLocation).where(CasinoLocation.group_id == group_id))
        s.commit()


def get_casino_location(group_id: int) -> int | None:
    """Return the registered topic_id for a group, or None if not registered."""
    with Session(engine) as s:
        row = s.get(CasinoLocation, group_id)
        return row.topic_id if row else None


def get_all_casino_locations() -> dict[int, int]:
    """Return {group_id: topic_id} for every registered group."""
    with Session(engine) as s:
        rows = s.scalars(select(CasinoLocation)).all()
        return {row.group_id: row.topic_id for row in rows}


# --- Pending reveals ---

def add_pending_reveal(chat_id: int, message_id: int, reveal_text: str, reveal_at: float) -> int:
    """Store a pending spoiler reveal. Returns the row id."""
    with Session(engine) as s:
        row = PendingReveal(chat_id=chat_id, message_id=message_id,
                            reveal_text=reveal_text, reveal_at=reveal_at)
        s.add(row)
        s.commit()
        return row.id


def get_pending_reveals() -> list[PendingReveal]:
    """Return all pending reveals (for re-scheduling after restart)."""
    with Session(engine) as s:
        return list(s.scalars(select(PendingReveal)).all())


def delete_pending_reveal(reveal_id: int) -> None:
    """Remove a pending reveal by id."""
    with Session(engine) as s:
        s.execute(delete(PendingReveal).where(PendingReveal.id == reveal_id))
        s.commit()
