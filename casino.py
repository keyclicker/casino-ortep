"""Slot-machine logic: decode Telegram dice values and calculate payouts."""
# Telegram's 🎰 slot machine sends a Dice with value 1-64.
# Each reel has 4 symbols: BAR=1, GRAPE=2, LEMON=3, SEVEN=4
# Encoding: value = (r1-1)*16 + (r2-1)*4 + (r3-1) + 1
#
# Math (64 equally likely outcomes, house edge = 10%):
#   Spin cost: $10. Expected return: $9.00  →  E[net] = -$1.00 per spin
#
# Outcome       count   return   contribution
# 7️⃣7️⃣7️⃣          1      $120      1.875
# 🍋🍋🍋 / 🍇🍇🍇    1 each   $28      0.4375 × 2
# BAR BAR BAR   1       $22      0.344
# Two 7️⃣         9       $12      1.688
# One 7️⃣         27       $8      3.375
# Pair (no 7️⃣)  18        $3      0.844
# Nothing        6        $0      0.000
#                                 ------
#                          total: $9.000

SPIN_COST = 10
DAILY_DEPOSIT = 20

SYMBOLS = {1: "BAR", 2: "🍇", 3: "🍋", 4: "7️⃣"}


def decode_reels(value: int) -> tuple[int, int, int]:
    """Decode a Telegram dice value (1-64) into three reel symbols (each 1-4)."""
    v = value - 1
    r3 = v % 4 + 1
    v //= 4
    r2 = v % 4 + 1
    r1 = v // 4 + 1
    return r1, r2, r3


def calculate_score(value: int) -> tuple[int, str]:  # pylint: disable=too-many-return-statements
    """Return (net_dollars, description). Net is negative when player loses."""
    r1, r2, r3 = decode_reels(value)
    reels_str = " ".join(SYMBOLS[r] for r in (r3, r2, r1))

    if r1 == r2 == r3 == 4:
        return 120 - SPIN_COST, f"{reels_str} — JACKPOT! 🎉"
    if r1 == r2 == r3 == 3:
        return 28 - SPIN_COST, f"{reels_str} — Three lemons!"
    if r1 == r2 == r3 == 2:
        return 28 - SPIN_COST, f"{reels_str} — Three grapes!"
    if r1 == r2 == r3 == 1:
        return 22 - SPIN_COST, f"{reels_str} — Triple BAR!"

    sevens = (r1, r2, r3).count(4)
    if sevens == 2:
        return 12 - SPIN_COST, f"{reels_str} — Two sevens!"
    if sevens == 1:
        return 8 - SPIN_COST, f"{reels_str} — One seven!"

    if r1 == r2 or r2 == r3 or r1 == r3:
        return 3 - SPIN_COST, f"{reels_str} — Pair!"

    return -SPIN_COST, f"{reels_str} — No luck this time."
