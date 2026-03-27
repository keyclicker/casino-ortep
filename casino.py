"""Slot-machine logic: decode Telegram dice values and calculate payouts."""
# Telegram's 🎰 slot machine sends a Dice with value 1-64.
# Each reel has 4 symbols: BAR=1, GRAPE=2, LEMON=3, SEVEN=4
# Encoding: value = (r1-1)*16 + (r2-1)*4 + (r3-1) + 1
#
# Math (64 equally likely outcomes, house edge ≈ 7.8%):
#   Spin cost: $10. Expected return: $9.22  →  E[net] = -$0.78 per spin
#
# Outcome         count   return   net      contribution
# 7️⃣7️⃣7️⃣           1      $300    +$290     +$290
# 🍋🍋🍋 / 🍇🍇🍇   1 each   $40    +$30      +$30 × 2
# 🅱🅱🅱            1       $30    +$20      +$20
# Two 7️⃣          9       $20    +$10      +$10 × 9  = +$90
# One 7️⃣         27        $0    -$10      -$10 × 27 = -$270
# Pair (no 7️⃣)   18        $0    -$10      -$10 × 18 = -$180
# Nothing         6        $0    -$10      -$10 × 6  = -$60
#                                           ------------------
#                                 total:    +$460 paid, -$510 collected
#                                 ratio:    1.109  (casino collects 10.9% more than pays)

SPIN_COST = 10
HOURLY_DEPOSIT = 10

SYMBOLS = {1: "🅱", 2: "🍇", 3: "🍋", 4: "7️⃣"}


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
        return 400 - SPIN_COST, f"{reels_str} — JACKPOT! 🎉"
    if r1 == r2 == r2 == 3:
        return 100 - SPIN_COST, f"{reels_str} — Three lemons!"
    if r1 == r2 == r3 == 2:
        return 100 - SPIN_COST, f"{reels_str} — Three grapes!"
    if r1 == r2 == r3 == 1:
        return 50 - SPIN_COST, f"{reels_str} — Triple BAR!"

    sevens = (r1, r2, r3).count(4)
    if sevens == 2:
        return 20 - SPIN_COST, f"{reels_str} — Two sevens!"
    if sevens == 1:
        return 10 - SPIN_COST, f"{reels_str} — So close! 😤"

    if r1 == r2 or r2 == r3 or r1 == r3:
        return 10 - SPIN_COST, f"{reels_str} — Pair!"

    return -SPIN_COST, f"{reels_str} — No luck this time."
