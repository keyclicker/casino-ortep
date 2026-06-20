import math

import casino
from casino import (
    CDM_MIN, CDM_MAX, SPIN_WINDOW,
    base_unit, calculate_score, compute_cdm, decode_reels,
)


# Helper: encode reels back to a dice value
def encode(r1, r2, r3) -> int:
    return (r1 - 1) * 16 + (r2 - 1) * 4 + (r3 - 1) + 1


class TestDecodeReels:
    def test_all_combinations_are_valid(self):
        for v in range(1, 65):
            r1, r2, r3 = decode_reels(v)
            assert all(1 <= r <= 4 for r in (r1, r2, r3))

    def test_roundtrip(self):
        for r1 in range(1, 5):
            for r2 in range(1, 5):
                for r3 in range(1, 5):
                    assert decode_reels(encode(r1, r2, r3)) == (r1, r2, r3)

    def test_boundaries(self):
        assert decode_reels(1) == (1, 1, 1)
        assert decode_reels(64) == (4, 4, 4)


class TestBaseUnit:
    def test_floor(self):
        assert base_unit(1) == casino.MIN_UNIT
        assert base_unit(0) == 0

    def test_scales_with_balance(self):
        assert base_unit(1000) == round(casino.STAKE_FRAC * 1000)
        assert base_unit(10_000) > base_unit(1000)

    def test_linear_in_balance(self):
        assert base_unit(10_000) == 10 * base_unit(1000)


class TestComputeCdm:
    def test_neutral_with_no_history(self):
        assert compute_cdm(0, 20, 0, 0) == 1.0

    def test_behind_gives_mercy(self):
        # big recent losses → cdm > 1
        cdm = compute_cdm(-10 * SPIN_WINDOW * 20, 20, -500, 1000)
        assert cdm > 1.0

    def test_ahead_gives_clawback(self):
        # big recent wins → cdm < 1
        cdm = compute_cdm(10 * SPIN_WINDOW * 20, 20, 500, 1000)
        assert cdm < 1.0

    def test_bounded(self):
        lo = compute_cdm(10**9, 20, 10**9, 1)
        hi = compute_cdm(-(10**9), 20, -(10**9), 1)
        assert lo == CDM_MIN
        assert hi == CDM_MAX

    def test_monotonic_in_recent(self):
        cdms = [compute_cdm(r, 20, 0, 0) for r in (-2000, -500, 0, 500, 2000)]
        assert cdms == sorted(cdms, reverse=True)  # more recent net → lower cdm

    def test_sign_hinge(self):
        # just above neutral → claw (<1); just below → mercy (>1)
        assert compute_cdm(1e-6, 20, 0, 0) < 1.0 < compute_cdm(-1e-6, 20, 0, 0)

    def test_mercy_stronger_than_claw_near_zero(self):
        # symmetric small signals: mercy lifts more than claw cuts (real comebacks)
        claw = 1 - compute_cdm(24, 20, 0, 0)
        mercy = compute_cdm(-24, 20, 0, 0) - 1
        assert mercy > claw > 0

    def test_tiny_base_no_division_error(self):
        # base 0 is guarded internally; must not raise and stays in band
        cdm = compute_cdm(-500, 0, -500, 0)
        assert CDM_MIN <= cdm <= CDM_MAX


class TestCalculateScore:
    B = 100  # base unit used in these tests

    def test_returns_net_price_desc(self):
        net, price, desc = calculate_score(encode(4, 4, 4), self.B, 1.0)
        assert isinstance(net, int) and isinstance(price, int) and isinstance(desc, str)

    def test_jackpot_is_big_win(self):
        net, price, desc = calculate_score(encode(4, 4, 4), self.B, 1.0)
        assert net > 0
        assert net == round(casino.WIN_M * casino.G_JACKPOT * self.B) - price
        assert "JACKPOT" in desc

    def test_three_lemons(self):
        net, price, _ = calculate_score(encode(3, 3, 3), self.B, 1.0)
        assert net == round(casino.WIN_M * casino.G_THREE_LEMON * self.B) - price

    def test_three_grapes(self):
        net, price, _ = calculate_score(encode(2, 2, 2), self.B, 1.0)
        assert net == round(casino.WIN_M * casino.G_THREE_GRAPE * self.B) - price

    def test_triple_bar_is_penalty(self):
        net, price, desc = calculate_score(encode(1, 1, 1), self.B, 1.0)
        loss = round(casino.PEN_T * casino.G_TRIPLE_BAR * self.B)
        assert net == -(price + loss)
        assert "PENALTY" in desc

    def test_double_bar_is_penalty(self):
        for reels in [(1, 1, 2), (1, 1, 3), (1, 2, 1), (2, 1, 1)]:
            net, price, desc = calculate_score(encode(*reels), self.B, 1.0)
            loss = round(casino.PEN_T * casino.G_DOUBLE_BAR * self.B)
            assert net == -(price + loss)
            assert "penalty" in desc.lower()

    def test_two_sevens(self):
        for reels in [(4, 4, 1), (4, 1, 4), (1, 4, 4), (4, 4, 3)]:
            net, price, desc = calculate_score(encode(*reels), self.B, 1.0)
            assert net == round(casino.WIN_M * casino.G_TWO_SEVENS * self.B) - price
            assert "two sevens" in desc.lower()

    def test_one_seven_near_break_even(self):
        net, _, desc = calculate_score(encode(4, 1, 2), self.B, 1.0)
        assert abs(net) <= self.B  # close to zero either way
        assert "close" in desc.lower()

    def test_no_match_loses_only_price(self):
        net, price, desc = calculate_score(encode(1, 2, 3), self.B, 1.0)
        assert net == -price
        assert "no luck" in desc.lower()

    def test_all_64_return_integers(self):
        for v in range(1, 65):
            net, price, desc = calculate_score(v, self.B, 1.0)
            assert isinstance(net, int) and isinstance(price, int)

    def test_zero_base_spin_is_inert(self):
        # balance <= 0 → base 0 → every money quantity rounds to 0 (free, no payout)
        for v in (1, 32, 64):  # penalty, mid, jackpot
            net, price, _ = calculate_score(v, 0, 1.0)
            assert net == 0 and price == 0

    def test_net_linear_in_base(self):
        # at a fixed cdm a 10x base gives ~10x net for the same outcome
        n1, _, _ = calculate_score(encode(4, 4, 4), 100, 1.0)
        n10, _, _ = calculate_score(encode(4, 4, 4), 1000, 1.0)
        assert math.isclose(n10, 10 * n1, rel_tol=0.01)

    def test_high_cdm_cheaper_price(self):
        _, p_lo, _ = calculate_score(encode(1, 2, 3), self.B, CDM_MAX)
        _, p_hi, _ = calculate_score(encode(1, 2, 3), self.B, CDM_MIN)
        assert p_lo < p_hi  # mercy (high cdm) → cheaper spin

    def test_high_cdm_bigger_win_smaller_penalty(self):
        win_lo, _, _ = calculate_score(encode(4, 4, 4), self.B, CDM_MIN)
        win_hi, _, _ = calculate_score(encode(4, 4, 4), self.B, CDM_MAX)
        assert win_hi > win_lo
        pen_lo, _, _ = calculate_score(encode(1, 1, 1), self.B, CDM_MIN)
        pen_hi, _, _ = calculate_score(encode(1, 1, 1), self.B, CDM_MAX)
        assert pen_hi > pen_lo  # less negative = softer penalty under mercy


class TestHouseEdge:
    B = 10_000  # large base to suppress rounding noise in the EV estimate

    def ev(self, cdm):
        """Average player net per spin over all 64 equally-likely outcomes."""
        total = sum(calculate_score(v, self.B, cdm)[0] for v in range(1, 65))
        return total / 64 / self.B  # in units of base B

    def test_small_house_edge_at_neutral(self):
        ev = self.ev(1.0)
        assert ev < 0                 # house-favoured at cdm=1
        assert ev > -0.06             # but only slightly (small advantage)

    def test_mercy_helps_player(self):
        assert self.ev(CDM_MAX) > self.ev(1.0)   # behind → better odds

    def test_clawback_hurts_player(self):
        assert self.ev(CDM_MIN) < self.ev(1.0)   # ahead → worse odds

    def test_ev_monotonic_in_cdm(self):
        evs = [self.ev(c) for c in (CDM_MIN, 0.95, 1.0, 1.1, CDM_MAX)]
        assert evs == sorted(evs)
