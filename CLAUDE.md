# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
poetry install                      # install deps
cp .env.example .env                # then fill BOT_TOKEN
poetry run python bot.py            # run bot (long-polling)

poetry run pytest                   # full suite
poetry run pytest tests/test_casino.py::TestDecodeReels::test_roundtrip -v  # single test
poetry run pytest --cov             # with coverage

poetry run alembic upgrade head     # apply migrations (also auto-run on bot startup via db.init_db)
poetry run alembic revision -m "msg"  # new migration
poetry run pylint *.py              # lint
```

## Architecture

Single-process Telegram bot. `bot.py` → builds `ApplicationBuilder`, runs `db.init_db()` (Alembic upgrade to head), registers handlers, starts long-polling. `post_init` sets bot commands, schedules hourly deposit job, and re-queues any `pending_reveals` rows left over from a prior run.

**Module layout (flat, no package):**
- `casino.py` — pure slot logic. `decode_reels()` unpacks Telegram dice 1–64 into 3 reels (4 symbols: BAR/GRAPE/LEMON/SEVEN). `calculate_score()` returns `(net, description)`. `get_spin_params(balance)` tiers cost/win/penalty multipliers by `balance // TIER_BALANCE_CAP`. Expected value ~+$9.77/spin at tier 0 (player-favoured). No DB/telegram imports — directly unit-testable.
- `db.py` — SQLAlchemy 2.0 ORM. Models: `Player` (user_id PK, balance, total_won/lost), `CasinoLocation` (group_id → topic_id), `PendingReveal` (persisted spoiler jobs). All queries open a fresh `Session(engine)` per call; `apply_spin()` is the atomic spin transaction.
- `handlers.py` — all Telegram command/message handlers. `handle_slot` is the core: applies spin, sends result under a spoiler entity (UTF-16 length required), persists `PendingReveal`, schedules `reveal_message` job.
- `jobs.py` — `reveal_message` (edits message to lift spoiler, deletes `pending_reveals` row), `job_hourly_deposit` (credits all players + announces in each registered casino topic).
- `filters.py` — `CasinoFilter`: slot messages accepted in private chats always; in groups only in the configured topic (or anywhere if none set).
- `helpers.py` — `BOT_ADMIN = "nick_keyclicker"` hardcoded; `SPOILER_DELAY = 2s`; `display_name`, `md_name` (markdown escape), `ensure_player`, `is_bot_admin`, `is_chat_admin`, `reply` (silent replies).

**Spoiler reveal persistence flow.** `pending_reveals` is the durability layer for in-flight spoiler reveal jobs — written before scheduling, deleted in the job's `finally`. On startup `_post_init` re-schedules any stragglers so reveals survive restarts.

**Topic gating.** `/settopic` (group-admin) binds a group to a single topic thread; `CasinoFilter` enforces it. Without it, the bot responds in all topics of the group.

**Bot-admin commands** (`/dodep`, `/balances`) silently return for non-admins — username match against `BOT_ADMIN` constant in `helpers.py`.

## Tests

Pytest. DB tests monkeypatch `db.engine` to `sqlite:///:memory:` and call `Base.metadata.create_all` directly (bypassing Alembic). When adding DB model changes, update both SQLAlchemy models in `db.py` AND add an Alembic migration in `alembic/versions/`.

## Configuration

- `.env`: `BOT_TOKEN` only.
- `casino.db` (SQLite, gitignored) created next to `bot.py`.
- Tunables live in `casino.py` as module constants (`SPIN_COST`, `TIER_*`, `PAYOUT_*`, `HOURLY_DEPOSIT`).
