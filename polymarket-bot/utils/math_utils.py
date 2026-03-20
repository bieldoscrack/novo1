"""
Math utilities: pair cost, Kelly criterion, etc.
"""
from __future__ import annotations


def pair_cost(
    qty_yes: float,
    qty_no: float,
    cost_yes: float,
    cost_no: float,
) -> float:
    """
    pair_cost = (total_cost_YES + total_cost_NO) / min(qty_YES, qty_NO)
    Returns 0 if no matched shares.
    """
    matched = min(qty_yes, qty_no)
    if matched <= 0:
        return 0.0
    return (cost_yes + cost_no) / matched


def new_pair_cost_after_buy(
    qty_yes: float,
    qty_no: float,
    cost_yes: float,
    cost_no: float,
    side: str,  # "YES" or "NO"
    delta_q: float,
    price: float,
) -> float:
    """Simulate new pair cost after adding delta_q shares on side."""
    new_cost = cost_yes + cost_no + price * delta_q
    if side == "YES":
        new_matched = min(qty_yes + delta_q, qty_no)
    else:
        new_matched = min(qty_yes, qty_no + delta_q)
    if new_matched <= 0:
        return 999.0
    return new_cost / new_matched


def kelly_fraction(win_prob: float, odds: float) -> float:
    """
    Kelly criterion: f = (p * b - q) / b
    where b = odds (payout per $1 risked), p = win prob, q = 1-p
    Returns 0 if negative (no edge).
    """
    if odds <= 0 or win_prob <= 0 or win_prob >= 1:
        return 0.0
    q = 1.0 - win_prob
    f = (win_prob * odds - q) / odds
    return max(0.0, f)


def implied_prob_from_price(price: float) -> float:
    """Polymarket price IS the implied probability (0–1)."""
    return max(0.0, min(1.0, price))


def edge(true_prob: float, market_price: float) -> float:
    """Edge = true probability - market implied probability."""
    return true_prob - market_price
