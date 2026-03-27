"""Slot-machine logic: decode Telegram dice values and calculate payouts."""
# Telegram's 🎰 slot machine sends a Dice with value 1-64.
# Each reel has 4 symbols: BAR=1, GRAPE=2, LEMON=3, SEVEN=4
# Encoding: value = (r1-1)*16 + (r2-1)*4 + (r3-1) + 1
#
# Base math (64 equally likely outcomes, tier 0, balance < 500):
#   Spin cost: $10. Expected return: $12.97  →  E[net] = +$2.97 per spin (player advantage)
#
# Outcome         count   return   net      contribution
# 7️⃣7️⃣7️⃣           1      $300    +$290     +$290
# 🍋🍋🍋 / 🍇🍇🍇   1 each   $50    +$40      +$40 × 2
# 🅱🅱🅱            1       $25    +$15      +$15
# Two 7️⃣          9       $20    +$10      +$10 × 9  = +$90
# One 7️⃣         27        $5    -$5       -$5  × 27 = -$135
# Pair (no 7️⃣)   18        $5    -$5       -$5  × 18 = -$90
# Nothing         6        $0    -$10      -$10 × 6  = -$60
#                                           ------------------
#                                 total:    +$475 paid, -$285 collected
#                                 ratio:    0.60  (casino pays out 40% more than it collects)
#
# Tier scaling (every TIER_BALANCE_CAP):
#   tier = balance // TIER_BALANCE_CAP
#   cost = SPIN_COST × TIER_COST_MULT^tier
#   win_mult = TIER_WIN_MULT^tier
#   balance ≥ 500  → tier 1: cost $20,  wins ×1.8
#   balance ≥ 1000 → tier 2: cost $40,  wins ×3.24
#   balance ≥ 1500 → tier 3: cost $80,  wins ×5.83
#   ...

SPIN_COST = 10
HOURLY_DEPOSIT = 20 

TIER_BALANCE_CAP = 500   # balance threshold per tier step
TIER_COST_MULT = 2.0       # cost multiplier per tier (×1.5 per step)
TIER_WIN_MULT = 1.8      # win multiplier per tier (×1.4 per step)

SYMBOLS = {1: "🅱", 2: "🍇", 3: "🍋", 4: "7️⃣"}


def get_spin_params(balance: int) -> tuple[int, float]:
    """Return (spin_cost, win_multiplier) for the given balance tier."""
    tier = balance // TIER_BALANCE_CAP
    if tier == 0:
        return SPIN_COST, 1.0
    return SPIN_COST * (TIER_COST_MULT ** tier), TIER_WIN_MULT ** tier


def decode_reels(value: int) -> tuple[int, int, int]:
    """Decode a Telegram dice value (1-64) into three reel symbols (each 1-4)."""
    v = value - 1
    r3 = v % 4 + 1
    v //= 4
    r2 = v % 4 + 1
    r1 = v // 4 + 1
    return r1, r2, r3


def calculate_score(value: int, cost: int = SPIN_COST, win_mult: float = 1.0) -> tuple[int, str]:  # pylint: disable=too-many-return-statements
    """Return (net_dollars, description). Net is negative when player loses."""
    r1, r2, r3 = decode_reels(value)
    reels_str = " ".join(SYMBOLS[r] for r in (r3, r2, r1))

    def pay(gross: int) -> int:
        return round(gross * win_mult) - cost

    if r1 == r2 == r3 == 4:
        return pay(400), f"{reels_str} — JACKPOT! 🎉"
    if r1 == r2 == r3 == 3:
        return pay(100), f"{reels_str} — Three lemons!"
    if r1 == r2 == r3 == 2:
        return pay(100), f"{reels_str} — Three grapes!"
    if r1 == r2 == r3 == 1:
        return pay(50), f"{reels_str} — Triple BAR!"

    sevens = (r1, r2, r3).count(4)
    if sevens == 2:
        return pay(25), f"{reels_str} — Two sevens!"
    if sevens == 1:
        return pay(10), f"{reels_str} — So close! 😤"

    if r1 == r2 or r2 == r3 or r1 == r3:
        return pay(10), f"{reels_str} — Pair!"

    return pay(0), f"{reels_str} — No luck this time."
