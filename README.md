# Casino Ortep

Telegram casino bot for group topics.

## Setup

```bash
poetry install
cp .env.example .env
# Fill in BOT_TOKEN in .env
poetry run python bot.py
```

## Configuration

- `BOT_TOKEN` — Telegram bot token from @BotFather

The casino topic is configured at runtime via the `/settopic` command (see below).

## Bot commands

| Command | Who | Description |
|---|---|---|
| `/balance` | anyone | Check your dollar balance |
| `/give @user amount` | anyone | Send dollars to another player |
| `/settopic` | group admin | Set the current topic as the casino |
| `/unsettopic` | group admin | Disable the casino topic |
| `/casino` | anyone | Show where the casino is active |

## Development

```bash
poetry run pytest
```
