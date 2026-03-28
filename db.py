"""Database layer — SQLAlchemy models and query helpers for the casino bot."""
from pathlib import Path
from sqlalchemy import create_engine, select, delete, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from alembic.config import Config
from alembic import command

DB_URL = "sqlite:///casino.db"
DEFAULT_BALANCE = 100

engine = create_engine(DB_URL)


class Base(DeclarativeBase):  # pylint: disable=too-few-public-methods
    """Declarative base for all ORM models."""


class Player(Base):  # pylint: disable=too-few-public-methods
    """A registered bot user and their balance."""
    __tablename__ = "players"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(default="")
    balance: Mapped[int] = mapped_column(default=0)
    total_won: Mapped[int] = mapped_column(default=0)
    total_lost: Mapped[int] = mapped_column(default=0)


class CasinoLocation(Base):  # pylint: disable=too-few-public-methods
    """A group (and its topic) where the casino is active."""
    __tablename__ = "casino_locations"

    group_id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int]


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


# --- Players ---

def get_or_create(user_id: int, username: str) -> int:
    """Return the player's balance, creating them with DEFAULT_BALANCE if new."""
    with Session(engine) as s:
        player = s.get(Player, user_id)
        if player is None:
            player = Player(user_id=user_id, username=username.lower(), balance=DEFAULT_BALANCE)
            s.add(player)
        else:
            player.username = username.lower()
        s.commit()
        return player.balance


def get_balance(user_id: int) -> int | None:
    """Return the player's balance, or None if they don't exist."""
    with Session(engine) as s:
        player = s.get(Player, user_id)
        return player.balance if player else None


def apply_spin(user_id: int, net: int, cost: int) -> tuple[bool, int]:
    """Atomically check the player can afford the spin and apply it.

    Returns (ok, new_balance). ok=False means insufficient funds; balance unchanged.
    """
    with Session(engine) as s:
        player = s.get(Player, user_id)
        if player is None or player.balance < cost:
            return False, (player.balance if player else 0)
        player.balance += net
        if net > 0:
            player.total_won += net
        elif net < 0:
            player.total_lost += abs(net)
        s.commit()
        return True, player.balance


def get_player_stats(user_id: int) -> tuple[int, int] | None:
    """Return (total_won, total_lost) for a player, or None if not found."""
    with Session(engine) as s:
        player = s.get(Player, user_id)
        if player is None:
            return None
        return player.total_won, player.total_lost


def get_casino_stats() -> tuple[int, int]:
    """Return (total_paid_out, total_collected) across all players."""
    with Session(engine) as s:
        paid_out = s.scalar(select(func.sum(Player.total_won))) or 0
        collected = s.scalar(select(func.sum(Player.total_lost))) or 0
        return paid_out, collected


def transfer(from_id: int, to_id: int, amount: int) -> tuple[int, int]:
    """Transfer amount from sender to receiver. Raises ValueError on bad state."""
    with Session(engine) as s:
        sender = s.get(Player, from_id)
        if sender is None or sender.balance < amount:
            raise ValueError("insufficient_funds")
        receiver = s.get(Player, to_id)
        if receiver is None:
            raise ValueError("receiver_not_found")
        sender.balance -= amount
        receiver.balance += amount
        s.commit()
        return sender.balance, receiver.balance


def get_by_username(username: str) -> Player | None:
    """Look up a player by username (leading @ stripped)."""
    with Session(engine) as s:
        needle = username.lstrip("@").lower()
        return s.scalars(
            select(Player).where(func.lower(Player.username) == needle)
        ).first()


def credit(user_id: int, amount: int) -> int:
    """Add amount to a specific player's balance unconditionally. Returns new balance."""
    with Session(engine) as s:
        player = s.get(Player, user_id)
        if player is None:
            raise ValueError("player_not_found")
        player.balance += amount
        s.commit()
        return player.balance


def get_all_players_by_balance() -> list[Player]:
    """Return all players sorted by balance descending."""
    with Session(engine) as s:
        return s.scalars(select(Player).order_by(Player.balance.desc())).all()


def daily_deposit(amount: int) -> int:
    """Add amount to every registered player's balance. Returns the player count."""
    with Session(engine) as s:
        players = s.scalars(select(Player)).all()
        for player in players:
            player.balance += amount
        s.commit()
        return len(players)


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
