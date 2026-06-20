"""Slot-machine logic: decode Telegram dice values and calculate payouts."""
# Telegram's 🎰 slot machine sends a Dice with value 1-64.
# Each reel has 4 symbols: BAR=1, GRAPE=2, LEMON=3, SEVEN=4
# Encoding: value = (r1-1)*16 + (r2-1)*4 + (r3-1) + 1
#
# Base math — 64 equally likely outcomes, at the reference balance (BALANCE_REF):
#
# Outcome                 count  payout    net      contribution
# 7️⃣7️⃣7️⃣ (jackpot)        1     $500    +$490         +$490
# 🍋🍋🍋                   1     $250    +$240         +$240
# 🍇🍇🍇                   1     $100     +$90          +$90
# Two 7️⃣                  9      $25     +$15         +$135
# One 7️⃣                 27      $10       $0            $0
# 🅱🅱🅱 (penalty)          1       —      -$40          -$40
# Two 🅱 (penalty)         6       —      -$20         -$120
# Pair (no 7️⃣/🅱🅱)       12       $5      -$5          -$60
# No match                 6       —      -$10          -$60
#                                                    ────────
#                                   E[net per spin]: +$10.50  (player-favoured)
#
# Balance scaling — stakes grow smoothly with balance via gentle power laws
# (sub-exponential, super-linear). Let s = balance / BALANCE_REF:
#   cost         = SPIN_COST × s ^ COST_EXP        capped at ½·balance     (≤ half net)
#   win_mult     =            s ^ WIN_EXP          (sub-cost: wins stay rewarding)
#   penalty_mult =            s ^ PENALTY_EXP      scales the BAR penalty bases
#   penalty_cap  = 0.8 × balance                   max total loss on any spin (≤ 80% net)
#
# Because win growth (s^0.5) is gentler than cost growth (s^1.3), the per-spin EV
# starts positive (player-favoured) at low balance and flips negative as balance
# rises — yet a jackpot still nets positive well past the crossover, so a win after
# a losing streak still tastes good. The caps are rare safety nets, not the norm:
# at BALANCE_REF=100 nothing is capped; cost reaches ½·balance only past ~21k.
#
# Examples (BALANCE_REF=100, COST_EXP=1.3, WIN_EXP=0.5, PENALTY_EXP=1.2):
#   balance  100 → cost $10,  win ×1.00,  triple-BAR loss $40
#   balance  700 → cost $125, win ×2.65,  triple-BAR loss $435
#   balance 3000 → cost $832, win ×5.48,  triple-BAR loss $2400 (0.8 cap)

SPIN_COST = 10           # base spin cost, also the cost floor / reference cost
HOURLY_DEPOSIT = 20

BALANCE_REF = 100        # reference balance — multipliers are 1.0 and cost = SPIN_COST here
COST_EXP = 1.3           # cost growth exponent (super-linear, sub-exponential)
WIN_EXP = 0.5            # win-multiplier growth exponent (gentler than cost)
PENALTY_EXP = 1.2        # penalty growth exponent
COST_CAP_FRAC = 0.5      # spin cost never exceeds this fraction of balance
PENALTY_CAP_FRAC = 0.8   # total loss on a spin never exceeds this fraction of balance

TRIPLE_BAR_PENALTY = 30   # triple BAR base penalty (loss = round(base * penalty_mult) + cost)
DOUBLE_BAR_PENALTY = 10   # double BAR base penalty (loss = round(base * penalty_mult) + cost)

PAYOUT_JACKPOT     = 500  # 7️⃣7️⃣7️⃣
PAYOUT_THREE_LEMON = 250  # 🍋🍋🍋
PAYOUT_THREE_GRAPE = 100  # 🍇🍇🍇
PAYOUT_TWO_SEVENS  = 25   # two 7️⃣
PAYOUT_ONE_SEVEN   = 10   # one 7️⃣
PAYOUT_PAIR        = 5    # any pair (no 7️⃣, no double 🅱)
PAYOUT_NOTHING     = 0    # no match

SYMBOLS = {1: "🅱", 2: "🍇", 3: "🍋", 4: "7️⃣"}


def get_spin_params(balance: int) -> tuple[int, float, float, int]:
    """Return (spin_cost, win_mult, penalty_mult, penalty_cap) for the given balance.

    All curves are gentle power laws of s = balance / BALANCE_REF (see module header).
    `spin_cost` is clamped to at most COST_CAP_FRAC of balance; `penalty_cap` is the
    hard ceiling on total loss for any single spin (PENALTY_CAP_FRAC of balance).
    """
    if balance <= 0:
        return 0, 0.0, 0.0, 0

    scale = balance / BALANCE_REF
    cost = round(SPIN_COST * scale ** COST_EXP)
    cost = min(int(balance * COST_CAP_FRAC), max(SPIN_COST, cost))

    win_mult = scale ** WIN_EXP
    penalty_mult = scale ** PENALTY_EXP
    penalty_cap = round(balance * PENALTY_CAP_FRAC)

    return cost, win_mult, penalty_mult, penalty_cap


def decode_reels(value: int) -> tuple[int, int, int]:
    """Decode a Telegram dice value (1-64) into three reel symbols (each 1-4)."""
    v = value - 1
    r3 = v % 4 + 1
    v //= 4
    r2 = v % 4 + 1
    r1 = v // 4 + 1
    return r1, r2, r3


def calculate_score(  # pylint: disable=too-many-return-statements
    value: int,
    cost: int = SPIN_COST,
    win_mult: float = 1.0,
    penalty_mult: float = 1.0,
    penalty_cap: int | None = None,
) -> tuple[int, str]:
    """Return (net_dollars, description). Net is negative when player loses.

    `penalty_cap`, when given, caps the *total* loss (penalty base + cost) on a
    penalty outcome so a single spin never costs more than that many dollars.
    """
    r1, r2, r3 = decode_reels(value)
    reels_str = " ".join(SYMBOLS[r] for r in (r3, r2, r1))

    def pay(gross: int) -> int:
        return round(gross * win_mult) - cost

    def penalty(base: int) -> int:
        loss = round(base * penalty_mult) + cost
        if penalty_cap is not None:
            loss = min(loss, penalty_cap)
        return -loss

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
