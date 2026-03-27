import pytest
from casino import SPIN_COST, calculate_score, decode_reels

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
        assert net == 110
        assert "JACKPOT" in desc

    def test_three_lemons(self):
        net, desc = calculate_score(encode(3, 3, 3))
        assert net == 18
        assert "lemon" in desc.lower()

    def test_three_grapes(self):
        net, desc = calculate_score(encode(2, 2, 2))
        assert net == 18
        assert "grape" in desc.lower()

    def test_triple_bar(self):
        net, desc = calculate_score(encode(1, 1, 1))
        assert net == 12
        assert "BAR" in desc

    @pytest.mark.parametrize("reels", [
        (4, 4, 1), (4, 4, 2), (4, 4, 3),
        (4, 1, 4), (4, 2, 4), (4, 3, 4),
        (1, 4, 4), (2, 4, 4), (3, 4, 4),
    ])
    def test_two_sevens(self, reels):
        net, desc = calculate_score(encode(*reels))
        assert net == 2
        assert "two sevens" in desc.lower()

    @pytest.mark.parametrize("reels", [
        (4, 1, 2), (4, 1, 3), (4, 2, 3),
        (1, 4, 2), (1, 4, 3), (2, 4, 3),
        (1, 2, 4), (1, 3, 4), (2, 3, 4),
    ])
    def test_one_seven(self, reels):
        net, desc = calculate_score(encode(*reels))
        assert net == -2
        assert "one seven" in desc.lower()

    @pytest.mark.parametrize("reels", [
        (1, 1, 2), (1, 1, 3),
        (2, 2, 1), (2, 2, 3),
        (3, 3, 1), (3, 3, 2),
        (1, 2, 1), (2, 1, 2),
        (1, 2, 2), (3, 1, 1),
    ])
    def test_pair_no_seven(self, reels):
        net, desc = calculate_score(encode(*reels))
        assert net == 3 - SPIN_COST
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
    def test_expected_return_is_nine(self):
        """Casino should expect $9 return per $10 spin (10% house edge)."""
        total_return = sum(calculate_score(v)[0] + SPIN_COST for v in range(1, 65))
        expected_return = total_return / 64
        assert abs(expected_return - 9.0) < 0.01

    def test_casino_wins_on_average(self):
        total_net = sum(calculate_score(v)[0] for v in range(1, 65))
        assert total_net < 0  # casino is net positive
