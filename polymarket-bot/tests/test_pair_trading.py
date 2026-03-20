"""Tests for pair trading math and logic."""
import pytest
from utils.math_utils import pair_cost, new_pair_cost_after_buy, kelly_fraction, edge


def test_pair_cost_basic():
    # 100 YES @ $0.48, 100 NO @ $0.50 -> cost = 98, matched = 100 -> pc = 0.98
    assert pair_cost(100, 100, 48.0, 50.0) == pytest.approx(0.98)


def test_pair_cost_unbalanced():
    # 100 YES, 80 NO -> matched = 80 -> cost=(cost_yes + cost_no)/80
    pc = pair_cost(100, 80, 48.0, 40.0)
    assert pc == pytest.approx((48.0 + 40.0) / 80)


def test_pair_cost_zero_matched():
    assert pair_cost(0, 100, 0, 50) == 0.0


def test_new_pair_cost_buy_yes():
    # Adding 50 YES @ $0.48 to 100/100 position with cost 48/50
    pc = new_pair_cost_after_buy(100, 100, 48.0, 50.0, "YES", 50, 0.48)
    # new_cost = 48+50 + 50*0.48 = 122, matched = min(150, 100) = 100
    assert pc == pytest.approx(122.0 / 100)


def test_kelly_basic():
    # 60% win chance, 1:1 odds (bet $1 to win $1)
    # Kelly = (0.6*1 - 0.4) / 1 = 0.2
    f = kelly_fraction(0.60, 1.0)
    assert f == pytest.approx(0.20)


def test_kelly_no_edge():
    f = kelly_fraction(0.50, 1.0)
    assert f == 0.0


def test_kelly_negative_clamped():
    f = kelly_fraction(0.30, 1.0)
    assert f == 0.0


def test_edge_calc():
    assert edge(0.65, 0.50) == pytest.approx(0.15)
    assert edge(0.40, 0.50) == pytest.approx(-0.10)
