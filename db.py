"""Database layer — SQLAlchemy models and query helpers for the casino bot."""
from sqlalchemy import create_engine, select, delete, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session

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


class CasinoLocation(Base):  # pylint: disable=too-few-public-methods
    """A group (and its topic) where the casino is active."""
    __tablename__ = "casino_locations"

    group_id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int]


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)


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


def apply_spin(user_id: int, net: int) -> tuple[bool, int]:
    """Atomically check the player can afford the spin and apply it.

    Returns (ok, new_balance). ok=False means insufficient funds; balance unchanged.
    """
    with Session(engine) as s:
        player = s.get(Player, user_id)
        if player is None or player.balance + net < 0:
            return False, (player.balance if player else 0)
        player.balance += net
        s.commit()
        return True, player.balance


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
