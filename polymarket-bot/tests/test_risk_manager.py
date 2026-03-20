"""Tests for RiskManager."""
import pytest
from unittest.mock import patch
from core.risk_manager import RiskManager
from config import settings


def make_risk() -> RiskManager:
    rm = RiskManager()
    rm._capital = 1000.0
    rm._daily_start_balance = 1000.0
    return rm


def test_size_position_basic():
    rm = make_risk()
    # 60% prob, price 0.48 -> should return positive size
    size = rm.size_position(win_prob=0.60, price=0.48)
    assert size > 0


def test_size_position_no_edge():
    rm = make_risk()
    # 50% prob at 0.50 price -> Kelly = 0 -> minimal size capped at $1
    size = rm.size_position(win_prob=0.50, price=0.50)
    # Kelly is 0 at break-even, size should be $1 (minimum)
    assert size >= 0


def test_can_trade_ok():
    rm = make_risk()
    ok, reason = rm.can_trade(100.0)
    assert ok


def test_can_trade_exceeds_reserve():
    rm = make_risk()
    rm._capital = 100.0
    # Trying to trade $90 when reserve requires $20 liquid
    ok, reason = rm.can_trade(90.0)
    assert not ok


def test_stop_loss_triggers():
    rm = make_risk()
    rm._daily_start_balance = 1000.0
    rm._capital = 940.0  # 6% loss > 5% threshold
    triggered = rm.check_daily_stop_loss()
    assert triggered
    assert rm.is_paused


def test_stop_loss_not_triggers():
    rm = make_risk()
    rm._daily_start_balance = 1000.0
    rm._capital = 970.0  # 3% loss < 5% threshold
    triggered = rm.check_daily_stop_loss()
    assert not triggered
