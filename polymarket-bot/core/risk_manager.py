"""
Risk management: position sizing, stop-loss, exposure limits.
"""
from __future__ import annotations
import time
from typing import Tuple

from loguru import logger

from config import settings
from core.state_manager import bot_state
from utils.math_utils import kelly_fraction


class RiskManager:
    def __init__(self) -> None:
        self._capital: float = settings.initial_capital
        self._daily_start_balance: float = settings.initial_capital
        self._daily_start_ts: float = time.time()
        self._paused: bool = False
        self._pause_until: float = 0.0

    # ── Balance tracking ──────────────────────────────────────────────────────

    def update_balance(self, balance: float) -> None:
        self._capital = balance
        now = time.time()
        # Reset daily at midnight UTC
        if now - self._daily_start_ts >= 86400:
            self._daily_start_balance = balance
            self._daily_start_ts = now

    # ── Pause / stop-loss ────────────────────────────────────────────────────

    @property
    def is_paused(self) -> bool:
        if self._paused and time.time() > self._pause_until:
            self._paused = False
            bot_state.add_log("Stop-loss expirou — bot resumido")
        return self._paused

    def check_daily_stop_loss(self) -> bool:
        """Returns True if stop-loss triggered."""
        if self._capital <= 0 or self._daily_start_balance <= 0:
            return False
        loss_pct = (self._daily_start_balance - self._capital) / self._daily_start_balance
        if loss_pct >= settings.stop_loss_daily_pct:
            self._paused = True
            self._pause_until = time.time() + settings.stop_loss_pause_hours * 3600
            msg = (
                f"STOP-LOSS: perda {loss_pct*100:.1f}% em 24h · "
                f"pausado por {settings.stop_loss_pause_hours}h"
            )
            bot_state.add_log(msg)
            logger.warning(msg)
            return True
        return False

    # ── Position sizing ───────────────────────────────────────────────────────

    def size_position(
        self,
        win_prob: float,
        price: float,
        market_exposure: float = 0.0,
    ) -> float:
        """
        Kelly-based position sizing with hard limits.
        Returns size in USDC.
        """
        if self.is_paused:
            return 0.0

        available = self._capital * (1 - settings.min_reserve_pct)
        max_total_exposure = self._capital * settings.max_exposure_pct
        room = max(0.0, max_total_exposure - market_exposure)
        available = min(available, room)

        if available <= 0:
            return 0.0

        # Kelly fraction
        if price <= 0 or price >= 1:
            return 0.0
        odds = (1 - price) / price  # payout odds per $ risked
        f = kelly_fraction(win_prob, odds)
        # Use half-Kelly for safety
        f = f * 0.5
        f = max(0.0, min(f, 0.10))  # cap at 10% of available

        size = available * f
        size = max(1.0, min(size, available * 0.10))  # $1 min, 10% max
        return round(size, 2)

    def can_trade(self, cost: float) -> Tuple[bool, str]:
        """Check if a trade of given cost is allowed."""
        if self.is_paused:
            return False, "bot pausado (stop-loss)"
        liquid = self._capital * settings.min_reserve_pct
        if self._capital - cost < liquid:
            return False, f"reserva mínima ${liquid:.0f} não mantida"
        state = bot_state.get_state()
        total_exp = sum(
            p.get("pair_cost", 0) * min(p.get("qty_yes", 0), p.get("qty_no", 0))
            for p in state.get("positions", [])
        )
        if total_exp + cost > self._capital * settings.max_exposure_pct:
            return False, "exposição máxima atingida"
        return True, ""
