"""
Module 1 — Asymmetric Pair Trading ("Gabagool").

Logic:
- Monitor crypto 5m/15m markets via CLOB WS + Binance WS simultaneously
- Detect mispricing when Polymarket lags Binance price
- Buy cheap side as MAKER order when pair_cost < threshold
- Cancel preemptively if Binance moves against open order
"""
from __future__ import annotations
import asyncio
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from config import settings
from core.binance_feed import BinanceFeed
from core.executor import Executor, Position
from core.market_discovery import MarketDiscovery
from core.orderbook import ClobOrderbookWS, OrderbookSnapshot
from core.risk_manager import RiskManager
from core.state_manager import bot_state
from strategies.base import BaseStrategy
from utils.math_utils import new_pair_cost_after_buy, edge


# Minimum edge required before placing order
MIN_EDGE = 0.03
# Minimum orderbook liquidity (in shares)
MIN_LIQUIDITY = 10.0
# How often to scan markets (seconds)
SCAN_INTERVAL = 5


class PairTradingStrategy(BaseStrategy):
    def __init__(
        self,
        binance: BinanceFeed,
        orderbook_ws: ClobOrderbookWS,
        discovery: MarketDiscovery,
        executor: Executor,
        risk: RiskManager,
    ) -> None:
        super().__init__()
        self._binance = binance
        self._ob_ws = orderbook_ws
        self._discovery = discovery
        self._executor = executor
        self._risk = risk
        self._last_btc_price: float = 0.0
        self._last_btc_ts: float = 0.0

        # Subscribe to orderbook updates
        self._ob_ws.on_update(self._on_book_update)
        # Subscribe to binance prices
        self._binance.on_price(self._on_binance_price)

    def _on_binance_price(self, prices: Dict[str, float]) -> None:
        btc = prices.get("BTC", 0)
        if btc > 0:
            direction = ""
            if self._last_btc_price > 0:
                delta = btc - self._last_btc_price
                if delta > 50:
                    direction = "BULLISH BTC"
                elif delta < -50:
                    direction = "BEARISH BTC"
                else:
                    direction = "NEUTRAL BTC"
            self._last_btc_price = btc
            self._last_btc_ts = time.time()
            if direction:
                bot_state.update(signal=direction)

    def _on_book_update(self, token_id: str, book: OrderbookSnapshot) -> None:
        # Trigger immediate evaluation when book updates
        pass  # Main loop handles this

    async def run(self) -> None:
        self._running = True
        bot_state.add_log("Pair Trading iniciado ✓")

        # Subscribe to markets as they're discovered
        while self._running:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"PairTrading scan erro: {e}")
            await asyncio.sleep(SCAN_INTERVAL)

    async def _scan_cycle(self) -> None:
        if self._risk.is_paused:
            return

        markets = self._discovery.markets
        if not markets:
            return

        # Subscribe to orderbooks for discovered markets
        for m in markets:
            token_id = m.get("token_id", "")
            if token_id:
                await self._ob_ws.add_subscription(token_id)

        # Update confidence metrics
        self._update_confidence()

        # Evaluate each market pair
        for m in markets:
            await self._evaluate_market(m)

    async def _evaluate_market(self, market: Dict[str, Any]) -> None:
        token_id = market.get("token_id", "")
        market_name = market.get("name", token_id[:12])

        if not token_id:
            return

        book = self._ob_ws.get_book(token_id)
        if not book:
            return

        best_ask = book.best_ask()
        best_bid = book.best_bid()
        if not best_ask or not best_bid:
            return

        # Get current position
        pos = self._executor.positions.get(market_name)

        # Determine which side is cheaper
        yes_price = best_ask  # cost to buy YES
        no_price = 1.0 - best_bid  # cost to buy NO (complement)

        # Estimate true probability from Binance signal
        true_prob = self._estimate_true_prob(market)
        if true_prob is None:
            return

        # Check edge
        yes_edge = edge(true_prob, yes_price)
        no_edge = edge(1 - true_prob, no_price)

        # Determine side to buy
        buy_side: Optional[str] = None
        buy_price: float = 0.0
        buy_edge: float = 0.0

        if yes_edge > MIN_EDGE and yes_edge > no_edge:
            buy_side, buy_price, buy_edge = "YES", yes_price, yes_edge
        elif no_edge > MIN_EDGE:
            buy_side, buy_price, buy_edge = "NO", no_price, no_edge

        if not buy_side:
            return

        # Simulate pair cost check
        qty_yes = pos.qty_yes if pos else 0.0
        qty_no = pos.qty_no if pos else 0.0
        cost_yes = pos.cost_yes if pos else 0.0
        cost_no = pos.cost_no if pos else 0.0

        size_usd = self._risk.size_position(
            win_prob=true_prob if buy_side == "YES" else 1 - true_prob,
            price=buy_price,
            market_exposure=(cost_yes + cost_no),
        )
        if size_usd <= 0:
            return

        delta_q = size_usd / buy_price

        new_pc = new_pair_cost_after_buy(
            qty_yes, qty_no, cost_yes, cost_no,
            buy_side, delta_q, buy_price
        )
        if new_pc >= settings.pair_cost_threshold:
            return

        can, reason = self._risk.can_trade(size_usd)
        if not can:
            return

        logger.debug(
            f"Pair edge: {market_name} BUY {buy_side} @{buy_price:.3f} "
            f"edge={buy_edge:.3f} new_pc={new_pc:.3f}"
        )

        await self._executor.place_maker_order(
            token_id=token_id,
            side=buy_side,
            price=buy_price,
            size=delta_q,
            market_name=market_name,
        )

        # Update average pair cost in state
        all_pos = self._executor.positions
        if all_pos:
            avg_pc = sum(
                p.pair_cost_val for p in all_pos.values() if p.pair_cost_val > 0
            ) / max(1, sum(1 for p in all_pos.values() if p.pair_cost_val > 0))
            bot_state.update(pair_cost_avg=round(avg_pc, 4))

    def _estimate_true_prob(self, market: Dict[str, Any]) -> Optional[float]:
        """
        Estimate true probability using Binance price signal.
        For crypto markets: if BTC is rising, UP markets have higher true prob.
        """
        name = market.get("name", "").lower()
        slug = market.get("slug", "").lower()
        combined = name + slug
        prices = self._binance.prices

        if not any(v > 0 for v in prices.values()):
            # No Binance data yet — use market price as-is
            return market.get("price", 0.5)

        # Very simplified: detect trend from price movement signal
        signal = bot_state.get_state().get("signal", "SCANNING")
        base_prob = market.get("price", 0.5)

        # Adjust based on directional signal
        if "btc" in combined or "bitcoin" in combined:
            if "BULLISH" in signal:
                return min(0.90, base_prob + 0.12)
            elif "BEARISH" in signal:
                return max(0.10, base_prob - 0.12)
        elif "eth" in combined or "ethereum" in combined:
            if "BULLISH" in signal:
                return min(0.88, base_prob + 0.10)
            elif "BEARISH" in signal:
                return max(0.12, base_prob - 0.10)
        elif "sol" in combined or "solana" in combined:
            if "BULLISH" in signal:
                return min(0.86, base_prob + 0.08)
            elif "BEARISH" in signal:
                return max(0.14, base_prob - 0.08)

        return base_prob

    def _update_confidence(self) -> None:
        prices = self._binance.prices
        has_binance = any(v > 0 for v in prices.values())
        has_markets = len(self._discovery.markets) > 0

        pair_edge_pct = 75 if has_binance and has_markets else 20
        liquidity_pct = 60 if has_markets else 10
        copy_pct = 45  # Updated by copy trading module

        bot_state.update(
            confidence=[
                {"label": "Pair Edge", "pct": pair_edge_pct, "color": "#00ff41"},
                {"label": "Liquidity", "pct": liquidity_pct, "color": "#00cc30"},
                {"label": "Copy Signal", "pct": copy_pct, "color": "#ff8800"},
            ]
        )
