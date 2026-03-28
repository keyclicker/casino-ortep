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

TIER_BALANCE_CAP = 250    # balance threshold per tier step
TIER_COST_MULT = 1.5      # cost multiplier per tier
TIER_WIN_MULT = 1.4       # win multiplier per tier
TIER_PENALTY_MULT = 2.5   # penalty multiplier per tier

TRIPLE_BAR_PENALTY = 50   # triple BAR base penalty (net = -(base * penalty_mult + cost))
DOUBLE_BAR_PENALTY = 15   # double BAR base penalty (net = -(base * penalty_mult + cost))

PAYOUT_JACKPOT     = 500  # 7️⃣7️⃣7️⃣
PAYOUT_THREE_LEMON = 250  # 🍋🍋🍋
PAYOUT_THREE_GRAPE = 100  # 🍇🍇🍇
PAYOUT_TWO_SEVENS  = 25   # two 7️⃣
PAYOUT_ONE_SEVEN   = 10   # one 7️⃣
PAYOUT_PAIR        = 5    # any pair (no 7️⃣, no double 🅱)
PAYOUT_NOTHING     = 0    # no match

SYMBOLS = {1: "🅱", 2: "🍇", 3: "🍋", 4: "7️⃣"}


def get_spin_params(balance: int) -> tuple[int, float, float]:
    """Return (spin_cost, win_multiplier, penalty_multiplier) for the given balance tier."""
    tier = balance // TIER_BALANCE_CAP
    if tier == 0:
        return SPIN_COST, 1.0, 1.0
    return (
        round(SPIN_COST * (TIER_COST_MULT ** tier)),
        TIER_WIN_MULT ** tier,
        TIER_PENALTY_MULT ** tier,
    )


def decode_reels(value: int) -> tuple[int, int, int]:
    """Decode a Telegram dice value (1-64) into three reel symbols (each 1-4)."""
    v = value - 1
    r3 = v % 4 + 1
    v //= 4
    r2 = v % 4 + 1
    r1 = v // 4 + 1
    return r1, r2, r3


def calculate_score(  # pylint: disable=too-many-return-statements
    value: int, cost: int = SPIN_COST, win_mult: float = 1.0, penalty_mult: float = 1.0
) -> tuple[int, str]:
    """Return (net_dollars, description). Net is negative when player loses."""
    r1, r2, r3 = decode_reels(value)
    reels_str = " ".join(SYMBOLS[r] for r in (r3, r2, r1))

    def pay(gross: int) -> int:
        return round(gross * win_mult) - cost

    def penalty(base: int) -> int:
        return -(round(base * penalty_mult) + cost)

    if r1 == r2 == r3 == 4:
        return pay(PAYOUT_JACKPOT),     f"{reels_str} — JACKPOT! 🎉"
    if r1 == r2 == r3 == 3:
        return pay(PAYOUT_THREE_LEMON), f"{reels_str} — Three lemons!"
    if r1 == r2 == r3 == 2:
        return pay(PAYOUT_THREE_GRAPE), f"{reels_str} — Three grapes!"
    if r1 == r2 == r3 == 1:
        return penalty(TRIPLE_BAR_PENALTY), f"{reels_str} — PENALTY! 💸"

    sevens = (r1, r2, r3).count(4)
    if sevens == 2:
        return pay(PAYOUT_TWO_SEVENS), f"{reels_str} — Two sevens!"
    if sevens == 1:
        return pay(PAYOUT_ONE_SEVEN),  f"{reels_str} — So close! 😤"

    bars = (r1, r2, r3).count(1)
    if bars == 2:
        return penalty(DOUBLE_BAR_PENALTY), f"{reels_str} — Double BAR penalty! 💸"

    if r1 == r2 or r2 == r3 or r1 == r3:
        return pay(PAYOUT_PAIR),    f"{reels_str} — Pair!"

    return pay(PAYOUT_NOTHING), f"{reels_str} — No luck this time."
