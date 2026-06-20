"""Slot-machine logic: decode Telegram dice, size stakes, and score spins.

The economy runs on **cdm** — "casino dynamic magic" — a per-player multiplier
driven by the player's own spin history. The three money quantities on a spin
are linear in a base stake unit `B`, bent by `cdm`:

    price   = PRICE_K · g=1  · B · cdm^(-PRICE_EXP)      # cost to spin
    payout  = WIN_M   · g_win · B · cdm^(+WIN_EXP)        # credited on a win
    penalty = PEN_T   · g_pen · B · cdm^(-PRICE_EXP)      # debited on a BAR loss

At cdm = 1 the payout factors are tuned so the cost+penalty side slightly
outweighs the win side — a small, permanent house edge (~ -2% EV per spin).
That alone guarantees the house wins over a long horizon (gambler's ruin).

`cdm` is where the manipulation lives. It reacts to how the player is doing:

    behind  -> cdm > 1  : cheaper spins, bigger wins, softer penalties (MERCY —
                          a comeback to stop the player quitting)
    ahead   -> cdm < 1  : pricier spins, smaller wins, harsher penalties (CLAW —
                          collect the winnings back)

The cdm→EV response is deliberately *damped* (fractional exponents, tight
[CDM_MIN, CDM_MAX] band): the mathematical edge stays small so players survive
for thousands of spins, while the *perceived* swing (visibly cheaper spins,
"hot machine" cues in handlers.py) carries the drama. A per-spin loss cap
(LOSS_CAP_FRAC, applied by the caller against balance) prevents variance
wipeouts, so mercy comebacks can actually land.

Pure logic — no DB or telegram imports, directly unit-testable. The caller
feeds in the player's recent/lifetime history (see db.get_spin_signals).
"""
import math

HOURLY_DEPOSIT = 20      # free credits dripped to every player each hour (the faucet)

# --- stake sizing: the base unit B scales with wealth ---
MIN_UNIT = 5             # floor on the base stake unit
STAKE_FRAC = 0.05        # base unit ≈ this fraction of balance

# --- base formula weights (cost+penalty side slightly > win side => house edge) ---
PRICE_K = 1.0
WIN_M = 1.0
PEN_T = 1.0

# --- cdm sensitivity (gentle exponents keep the edge small and survivable) ---
PRICE_EXP = 0.30         # price & penalty scale as cdm ** -PRICE_EXP
WIN_EXP = 0.33           # payout scales as cdm ** +WIN_EXP

# --- cdm controller ---
SPIN_WINDOW = 12         # how many recent spins feed the short-term signal
CDM_MIN = 0.88           # max claw-back (player far ahead)
CDM_MAX = 1.22           # max mercy (player far behind)
CLAW_GAIN = 0.30         # strength of claw-back when ahead
MERCY_GAIN = 0.52        # strength of mercy when behind (stronger: real comebacks)
LIFE_BLEND = 0.18        # weight of lifetime position vs the recent window
LIFE_SCALE = 4.0         # amplifies the (small) per-unit lifetime edge signal

# --- safety ---
LOSS_CAP_FRAC = 0.5      # a single spin never costs more than this fraction of balance

# --- gross payout factors g (multiples of the base unit B) ---
G_JACKPOT     = 8.0      # 7️⃣7️⃣7️⃣
G_THREE_LEMON = 4.0      # 🍋🍋🍋
G_THREE_GRAPE = 3.0      # 🍇🍇🍇
G_TWO_SEVENS  = 1.8      # two 7️⃣
G_ONE_SEVEN   = 1.10     # one 7️⃣ (near break-even)
G_PAIR        = 0.95     # any pair
# penalty bases
G_TRIPLE_BAR  = 2.5      # 🅱🅱🅱
G_DOUBLE_BAR  = 1.2      # two 🅱

SYMBOLS = {1: "🅱", 2: "🍇", 3: "🍋", 4: "7️⃣"}


def base_unit(balance: int) -> int:
    """Return the base stake unit B for a balance — stakes grow with wealth."""
    if balance <= 0:
        return 0
    return max(MIN_UNIT, round(STAKE_FRAC * balance))


def compute_cdm(recent_net: float, base: int, life_net: float, life_staked: float) -> float:
    """Return the casino dynamic magic multiplier for a player's situation.

    `recent_net` is the summed net of the last SPIN_WINDOW spins; `life_net` /
    `life_staked` are the player's lifetime net result and total amount staked.
    Behind (losing) → cdm > 1 (mercy); ahead (winning) → cdm < 1 (claw-back).
    """
    base = max(1, base)
    short = (recent_net / SPIN_WINDOW) / base
    life = life_net / max(1.0, life_staked)
    r = (1 - LIFE_BLEND) * short + LIFE_BLEND * (LIFE_SCALE * life)
    if r >= 0:                              # player ahead → claw back
        cdm = 1 - CLAW_GAIN * math.tanh(r)
    else:                                   # player behind → mercy
        cdm = 1 + MERCY_GAIN * math.tanh(-r)
    return max(CDM_MIN, min(CDM_MAX, cdm))


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
    base: int,
    cdm: float = 1.0,
) -> tuple[int, int, str]:
    """Return (net, price, description) for a spin.

    `base` is the stake unit B (see base_unit); `cdm` bends the economy. `price`
    is what the spin costs (always paid); `net` is the balance delta (negative on
    a loss). The caller is responsible for the per-spin loss cap and affordability.
    """
    r1, r2, r3 = decode_reels(value)
    reels_str = " ".join(SYMBOLS[r] for r in (r3, r2, r1))

    price = round(PRICE_K * base * cdm ** (-PRICE_EXP))

    def win(g: float, label: str) -> tuple[int, int, str]:
        payout = round(WIN_M * g * base * cdm ** WIN_EXP)
        return payout - price, price, f"{reels_str} — {label}"

    def pen(g: float, label: str) -> tuple[int, int, str]:
        loss = round(PEN_T * g * base * cdm ** (-PRICE_EXP))
        return -(price + loss), price, f"{reels_str} — {label}"

    if r1 == r2 == r3 == 4:
        return win(G_JACKPOT,     "JACKPOT! 🎉")
    if r1 == r2 == r3 == 3:
        return win(G_THREE_LEMON, "Three lemons!")
    if r1 == r2 == r3 == 2:
        return win(G_THREE_GRAPE, "Three grapes!")
    if r1 == r2 == r3 == 1:
        return pen(G_TRIPLE_BAR,  "PENALTY! 💸")

    sevens = (r1, r2, r3).count(4)
    if sevens == 2:
        return win(G_TWO_SEVENS, "Two sevens!")
    if sevens == 1:
        return win(G_ONE_SEVEN,  "So close! 😤")

    bars = (r1, r2, r3).count(1)
    if bars == 2:
        return pen(G_DOUBLE_BAR, "Double BAR penalty! 💸")

    if r1 == r2 or r2 == r3 or r1 == r3:
        return win(G_PAIR, "Pair!")

    return -price, price, f"{reels_str} — No luck this time."
