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
- `casino.py` — pure slot logic, built around **cdm** ("casino dynamic magic"). `decode_reels()` unpacks Telegram dice 1–64 into 3 reels (4 symbols: BAR/GRAPE/LEMON/SEVEN). `base_unit(balance)` sizes the stake unit `B` (≈5% of balance, floored). `compute_cdm(recent_net, base, life_net, life_staked)` returns a per-player multiplier in `[CDM_MIN, CDM_MAX]` — driven by the player's own spin history: losing → cdm>1 (mercy: cheaper spins, bigger wins, softer penalties), winning → cdm<1 (claw-back). `calculate_score(value, base, cdm)` returns `(net, price, description)`: `price/penalty = …·cdm^-PRICE_EXP`, `payout = …·cdm^+WIN_EXP`. Base EV is a small house edge (~−2% at cdm=1); the cdm→EV response is deliberately damped (fractional exponents + tight band) so players survive thousands of spins while perception carries the drama. Per-spin loss capped at `LOSS_CAP_FRAC` of balance by the caller. No DB/telegram imports — directly unit-testable. Design validated by Monte-Carlo: ~94% of players net house-positive, 0% broke, ~78% triple their money at some peak.
- `db.py` — SQLAlchemy 2.0 ORM. **`Ledger` is the single source of truth for the economy**: every balance-moving event is an immutable signed row tagged with a `LedgerKind` (`signup`, `spin_cost`, `spin_win`, `spin_penalty`, `give_out`/`give_in`, `dodep_admin`, `dodep_casino`). `Player.balance` is a *cache* of the player's ledger sum, updated in the same transaction via `_post()`; `recompute_balance()` rebuilds it from the ledger. **All nets/stats derive from the ledger only** — there are no `total_won/total_lost` counters. Other models: `CasinoLocation` (group_id → topic_id), `PlayerGroup` (many-to-many user↔group membership, composite PK), `PendingReveal` (persisted spoiler jobs). Key helpers: `apply_spin()` (atomic; writes a SPIN_COST row + a SPIN_WIN/SPIN_PENALTY row decomposing `net`), `get_spin_signals(user_id, window)` → `(recent_net, life_net, life_staked)` for `compute_cdm` (all from spin-kind rows), `get_player_stats`/`get_casino_stats` (won = gross payouts, lost = gross spent), `get_leaderboard(group_id=None)` (group-scoped when given, else global), `get_player_highlights(user_id)` → `(biggest_win, streak_len, streak_is_win)` (per-spin nets rebuilt from spin rows; feeds `/stats`), `record_membership`/`get_user_groups`/`get_group_members`, `get_ledger(user_id, limit)` (feeds the `/history` feed). All queries open a fresh `Session(engine)` per call.
- `handlers.py` — all Telegram command/message handlers. `handle_slot` is the core: applies spin, sends result under a spoiler entity (UTF-16 length required), persists `PendingReveal`, schedules `reveal_message` job.
- `jobs.py` — `reveal_message` (edits message to lift spoiler, deletes `pending_reveals` row), `job_hourly_deposit` (credits all players + announces in each registered casino topic).
- `filters.py` — `CasinoFilter`: slot messages accepted in private chats always; in groups only in the configured topic (or anywhere if none set).
- `helpers.py` — `BOT_ADMIN = "nick_keyclicker"` hardcoded; `SPOILER_DELAY = 2s`; `display_name`, `md_name` (markdown escape), `ensure_player`, `is_bot_admin`, `is_chat_admin`, `reply` (silent replies).

**Spoiler reveal persistence flow.** `pending_reveals` is the durability layer for in-flight spoiler reveal jobs — written before scheduling, deleted in the job's `finally`. On startup `_post_init` re-schedules any stragglers so reveals survive restarts.

**Topic gating.** `/settopic` (group-admin) binds a group to a single topic thread; `CasinoFilter` enforces it. Without it, the bot responds in all topics of the group.

**Bot-admin commands** (`/dodep`, `/all_balances`) silently return for non-admins — username match against `BOT_ADMIN` constant in `helpers.py`. `/balances` is public but **group-scoped**: it lists only the current group's members (via `PlayerGroup`) and gives a hint in private chats; `/all_balances` (admin) is the global leaderboard. Membership is recorded by `helpers.note_membership(update)`, called from `handle_slot`/`cmd_give`/`cmd_stats`/`cmd_balances` whenever a player acts in a group.

## Tests

Pytest. DB tests monkeypatch `db.engine` to `sqlite:///:memory:` and call `Base.metadata.create_all` directly (bypassing Alembic). When adding DB model changes, update both SQLAlchemy models in `db.py` AND add an Alembic migration in `alembic/versions/`.

## Configuration

- `.env`: `BOT_TOKEN` only.
- `casino.db` (SQLite, gitignored) created next to `bot.py`.
- Tunables live in `casino.py` as module constants: stake sizing (`MIN_UNIT`, `STAKE_FRAC`), cdm controller (`SPIN_WINDOW`, `CDM_MIN/MAX`, `CLAW_GAIN`, `MERCY_GAIN`, `LIFE_BLEND`), sensitivity exponents (`PRICE_EXP`, `WIN_EXP`), payout factors (`G_*`), `LOSS_CAP_FRAC`, `HOURLY_DEPOSIT`.
