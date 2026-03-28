"""Slot-machine logic: decode Telegram dice values and calculate payouts."""
# Telegram's 🎰 slot machine sends a Dice with value 1-64.
# Each reel has 4 symbols: BAR=1, GRAPE=2, LEMON=3, SEVEN=4
# Encoding: value = (r1-1)*16 + (r2-1)*4 + (r3-1) + 1
#
# Base math — 64 equally likely outcomes, tier 0 (balance < TIER_BALANCE_CAP):
#
# Outcome                 count  payout    net      contribution
# 7️⃣7️⃣7️⃣ (jackpot)        1     $500    +$490         +$490
# 🍋🍋🍋                   1     $250    +$240         +$240
# 🍇🍇🍇                   1     $100     +$90          +$90
# Two 7️⃣                  9      $25     +$15         +$135
# One 7️⃣                 27      $10       $0            $0
# 🅱🅱🅱 (penalty)          1       —      -$60          -$60
# Two 🅱 (penalty)         6       —      -$25         -$150
# Pair (no 7️⃣/🅱🅱)       12       $5      -$5          -$60
# No match                 6       —      -$10          -$60
#                                                    ────────
#                                   E[net per spin]:  +$9.77  (player-favoured)
#
# Tier scaling — every TIER_BALANCE_CAP coins the stakes increase:
#   tier         = balance // TIER_BALANCE_CAP
#   cost         = SPIN_COST      × TIER_COST_MULT    ^ tier
#   win_mult     = TIER_WIN_MULT  ^ tier
#   penalty_mult = TIER_PENALTY_MULT ^ tier
#
# Examples (TIER_BALANCE_CAP=250, TIER_COST_MULT=1.5, TIER_WIN_MULT=1.4):
#   balance   0–249  → tier 0: cost $10, wins ×1.0
#   balance 250–499  → tier 1: cost $15, wins ×1.4
#   balance 500–749  → tier 2: cost $22, wins ×1.96
#   balance 750+     → tier 3: cost $34, wins ×2.74  …

SPIN_COST = 10
HOURLY_DEPOSIT = 20

TIER_BALANCE_CAP = 100    # balance threshold per tier step
TIER_COST_MULT = 1.1      # cost multiplier per tier
TIER_WIN_MULT = 1.075       # win multiplier per tier
TIER_PENALTY_MULT = 1.15   # penalty multiplier per tier

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
