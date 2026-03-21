from __future__ import annotations
import time
from threading import Lock
from typing import Any, Dict, List


class BotState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._state: Dict[str, Any] = {
            "_ts": 0,
            "pnl": 0.0,
            "session_label": "sessão #1 · 0h ativa",
            "balance": 500.0,
            "active_bets": 0,
            "wins": 0,
            "losses": 0,
            "total_bets": 0,
            "signal": "SCANNING",
            "pair_cost_avg": 0.0,
            "simulation_mode": True,
            "uptime_seconds": 0,
            "log": [],
            "markets": [],
            "confidence": [
                {"label": "Pair Edge", "pct": 0, "color": "#00ff41"},
                {"label": "Liquidity", "pct": 0, "color": "#00cc30"},
                {"label": "Copy Signal", "pct": 0, "color": "#ff8800"},
            ],
            "resolved": [],
            "pnl_history": [0.0],
            "copy_trades": [],
            "positions": [],
            "binance_prices": {"BTC": 0.0, "ETH": 0.0, "SOL": 0.0},
            "ice_fishing": {
                "active_holes": 0,
                "max_holes": 10,
                "fish_caught": 0,
                "holes_drilled": 0,
                "holes_expired": 0,
                "holes": [],
            },
        }
        self._start_time: float = time.time()
        self._session_number: int = 1

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            self._state.update(kwargs)
            self._state["_ts"] = int(time.time())
            uptime = int(time.time() - self._start_time)
            self._state["uptime_seconds"] = uptime
            hours = uptime // 3600
            self._state["session_label"] = (
                f"sessão #{self._session_number} · {hours}h ativa"
            )

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            state["uptime_seconds"] = int(time.time() - self._start_time)
            hours = state["uptime_seconds"] // 3600
            state["session_label"] = (
                f"sessão #{self._session_number} · {hours}h ativa"
            )
            return state

    def add_log(self, msg: str) -> None:
        with self._lock:
            ts = time.strftime("%H:%M:%S", time.gmtime())
            self._state["log"].append(f"{ts} · {msg}")
            if len(self._state["log"]) > 200:
                self._state["log"] = self._state["log"][-200:]
            self._state["_ts"] = int(time.time())

    def add_resolved(self, market: str, outcome: str, pnl: float) -> None:
        with self._lock:
            self._state["resolved"].append(
                {"market": market, "outcome": outcome, "pnl": round(pnl, 4)}
            )
            if len(self._state["resolved"]) > 50:
                self._state["resolved"] = self._state["resolved"][-50:]
            self._state["pnl"] = round(self._state["pnl"] + pnl, 4)
            self._state["balance"] = round(self._state["balance"] + pnl, 4)
            self._state["pnl_history"].append(round(self._state["pnl"], 4))
            if len(self._state["pnl_history"]) > 80:
                self._state["pnl_history"] = self._state["pnl_history"][-80:]
            if pnl > 0:
                self._state["wins"] += 1
            else:
                self._state["losses"] += 1
            self._state["total_bets"] = (
                self._state["wins"] + self._state["losses"]
            )
            self._state["_ts"] = int(time.time())

    def update_binance_prices(self, prices: Dict[str, float]) -> None:
        with self._lock:
            self._state["binance_prices"].update(prices)
            self._state["_ts"] = int(time.time())

    def add_copy_trade(
        self, wallet: str, market: str, side: str, amount: float
    ) -> None:
        with self._lock:
            ts = time.strftime("%H:%M:%S", time.gmtime())
            self._state["copy_trades"].append(
                {
                    "wallet": f"{wallet[:6]}...{wallet[-4:]}",
                    "market": market,
                    "side": side,
                    "amount": round(amount, 2),
                    "time": ts,
                }
            )
            if len(self._state["copy_trades"]) > 20:
                self._state["copy_trades"] = self._state["copy_trades"][-20:]
            self._state["_ts"] = int(time.time())


# Singleton compartilhado por todo o processo
bot_state = BotState()
