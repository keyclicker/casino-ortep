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

    def test_tier_scaling_doubles_cost(self):
        from casino import get_spin_params, SPIN_COST, TIER_BALANCE_CAP, TIER_COST_MULT
        cost0, _, _ = get_spin_params(0)
        cost1, _, _ = get_spin_params(TIER_BALANCE_CAP)
        assert cost0 == SPIN_COST
        assert cost1 == SPIN_COST * TIER_COST_MULT

    def test_tier_scaling_multiplies_wins(self):
        from casino import get_spin_params, TIER_BALANCE_CAP, TIER_WIN_MULT
        _, mult0, _ = get_spin_params(0)
        _, mult1, _ = get_spin_params(TIER_BALANCE_CAP)
        assert mult0 == 1.0
        assert abs(mult1 - TIER_WIN_MULT) < 1e-9

    def test_tier_scaling_multiplies_penalties(self):
        from casino import get_spin_params, TIER_BALANCE_CAP, TIER_PENALTY_MULT
        _, _, pmult0 = get_spin_params(0)
        _, _, pmult1 = get_spin_params(TIER_BALANCE_CAP)
        assert pmult0 == 1.0
        assert abs(pmult1 - TIER_PENALTY_MULT) < 1e-9

    def test_tier_boundary(self):
        from casino import get_spin_params, SPIN_COST, TIER_BALANCE_CAP, TIER_COST_MULT
        cost_below, _, _ = get_spin_params(TIER_BALANCE_CAP - 1)
        cost_at, _, _    = get_spin_params(TIER_BALANCE_CAP)
        assert cost_below == SPIN_COST
        assert cost_at    == SPIN_COST * TIER_COST_MULT
