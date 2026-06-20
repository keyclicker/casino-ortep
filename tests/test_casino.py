import pytest
from casino import (
    SPIN_COST, TRIPLE_BAR_PENALTY, DOUBLE_BAR_PENALTY,
    PAYOUT_JACKPOT, PAYOUT_THREE_LEMON, PAYOUT_THREE_GRAPE,
    PAYOUT_TWO_SEVENS, PAYOUT_ONE_SEVEN, PAYOUT_PAIR, PAYOUT_NOTHING,
    calculate_score, decode_reels,
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


class TestCalculateScore:
    def test_jackpot(self):
        net, desc = calculate_score(encode(4, 4, 4))
        assert net == PAYOUT_JACKPOT - SPIN_COST
        assert "JACKPOT" in desc

    def test_three_lemons(self):
        net, desc = calculate_score(encode(3, 3, 3))
        assert net == PAYOUT_THREE_LEMON - SPIN_COST
        assert "lemon" in desc.lower()

    def test_three_grapes(self):
        net, desc = calculate_score(encode(2, 2, 2))
        assert net == PAYOUT_THREE_GRAPE - SPIN_COST
        assert "grape" in desc.lower()

    def test_triple_bar_penalty(self):
        from casino import TRIPLE_BAR_PENALTY
        net, desc = calculate_score(encode(1, 1, 1))
        assert net == -(TRIPLE_BAR_PENALTY + SPIN_COST)
        assert "PENALTY" in desc

    @pytest.mark.parametrize("reels", [
        (1, 1, 2), (1, 1, 3), (1, 2, 1), (2, 1, 1),
    ])
    def test_double_bar_penalty(self, reels):
        from casino import DOUBLE_BAR_PENALTY
        net, desc = calculate_score(encode(*reels))
        assert net == -(DOUBLE_BAR_PENALTY + SPIN_COST)
        assert "penalty" in desc.lower()

    @pytest.mark.parametrize("reels", [
        (4, 4, 1), (4, 4, 2), (4, 4, 3),
        (4, 1, 4), (4, 2, 4), (4, 3, 4),
        (1, 4, 4), (2, 4, 4), (3, 4, 4),
    ])
    def test_two_sevens(self, reels):
        net, desc = calculate_score(encode(*reels))
        assert net == PAYOUT_TWO_SEVENS - SPIN_COST
        assert "two sevens" in desc.lower()

    @pytest.mark.parametrize("reels", [
        (4, 1, 2), (4, 1, 3), (4, 2, 3),
        (1, 4, 2), (1, 4, 3), (2, 4, 3),
        (1, 2, 4), (1, 3, 4), (2, 3, 4),
    ])
    def test_one_seven(self, reels):
        net, desc = calculate_score(encode(*reels))
        assert net == PAYOUT_ONE_SEVEN - SPIN_COST
        assert "close" in desc.lower()

    @pytest.mark.parametrize("reels", [
        (2, 2, 1), (2, 2, 3),
        (3, 3, 1), (3, 3, 2),
        (2, 1, 2), (1, 2, 2),
    ])
    def test_pair_no_seven(self, reels):
        net, desc = calculate_score(encode(*reels))
        assert net == PAYOUT_PAIR - SPIN_COST
        assert "pair" in desc.lower()

    @pytest.mark.parametrize("reels", [
        (1, 2, 3), (1, 3, 2), (2, 1, 3), (2, 3, 1), (3, 1, 2), (3, 2, 1),
    ])
    def test_no_match(self, reels):
        net, desc = calculate_score(encode(*reels))
        assert net == -SPIN_COST
        assert "no luck" in desc.lower()

    def test_all_64_values_return_valid_result(self):
        for v in range(1, 65):
            net, desc = calculate_score(v)
            assert isinstance(net, int)
            assert isinstance(desc, str)


class TestHouseEdge:
    def test_all_outcomes_return_integers(self):
        """All 64 outcomes must produce integer net values (no rounding errors)."""
        for v in range(1, 65):
            net, _ = calculate_score(v)
            assert isinstance(net, int)

    def test_reference_balance_is_neutral(self):
        """At BALANCE_REF the cost is the base and both multipliers are 1.0."""
        from casino import get_spin_params, SPIN_COST, BALANCE_REF
        cost, win_mult, pen_mult, _ = get_spin_params(BALANCE_REF)
        assert cost == SPIN_COST
        assert abs(win_mult - 1.0) < 1e-9
        assert abs(pen_mult - 1.0) < 1e-9

    def test_cost_grows_super_linearly(self):
        """Cost rises faster than linear (COST_EXP > 1) but stays a power law, not exponential."""
        from casino import get_spin_params, BALANCE_REF
        c1, *_ = get_spin_params(BALANCE_REF)
        c10, *_ = get_spin_params(BALANCE_REF * 10)
        c100, *_ = get_spin_params(BALANCE_REF * 100)
        # 10x balance -> more than 10x cost (super-linear)
        assert c10 > c1 * 10
        # but the growth ratio is bounded/stable (power law), not accelerating like an exponential
        assert (c100 / c10) < (c10 / c1) * 2

    def test_cost_never_exceeds_half_balance(self):
        from casino import get_spin_params, COST_CAP_FRAC
        for bal in (50, 100, 500, 2000, 50_000, 500_000):
            cost, *_ = get_spin_params(bal)
            assert cost <= bal * COST_CAP_FRAC

    def test_wins_scale_slower_than_cost(self):
        """Win multiplier grows, but gentler than cost — so cost overtakes wins."""
        from casino import get_spin_params, BALANCE_REF
        c1, w1, *_ = get_spin_params(BALANCE_REF)
        c10, w10, *_ = get_spin_params(BALANCE_REF * 10)
        assert w10 > w1                      # wins still grow (taste of victory)
        assert (c10 / c1) > (w10 / w1)       # but cost grows faster

    def test_penalty_capped_at_80_percent(self):
        """Total loss on a penalty spin never exceeds PENALTY_CAP_FRAC of balance."""
        from casino import (
            get_spin_params, calculate_score, PENALTY_CAP_FRAC,
        )
        for bal in (200, 1000, 5000, 50_000):
            cost, win_mult, pen_mult, pen_cap = get_spin_params(bal)
            triple, _ = calculate_score(encode(1, 1, 1), cost, win_mult, pen_mult, pen_cap)
            assert -triple <= bal * PENALTY_CAP_FRAC

    def test_house_edge_flips_with_balance(self):
        """Player-favoured (EV>0) at low balance, house-favoured (EV<0) at high balance."""
        from casino import get_spin_params, calculate_score

        def ev(bal):
            cost, win_mult, pen_mult, pen_cap = get_spin_params(bal)
            total = sum(
                calculate_score(v, cost, win_mult, pen_mult, pen_cap)[0]
                for v in range(1, 65)
            )
            return total / 64

        assert ev(100) > 0      # new player: player-favoured
        assert ev(5000) < 0     # whale: house-favoured
