"""
Module 2 — Copy Trading.

Monitors whale wallets via Data API and replicates trades in
non-crypto (politics, macro, tech) markets.
"""
from __future__ import annotations
import asyncio
import time
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from config import settings
from core.executor import Executor
from core.polymarket_client import PolymarketClient
from core.risk_manager import RiskManager
from core.state_manager import bot_state
from strategies.base import BaseStrategy

POLL_INTERVAL = 30  # seconds between Data API polls
MIN_TRADE_SIZE = 50.0  # ignore trades < $50
MAX_PRICE_MOVE = 0.03  # don't copy if price moved > 3%
MAX_MARKET_EXPOSURE_PCT = 0.10  # max 10% capital per market


class CopyTradingStrategy(BaseStrategy):
    def __init__(
        self,
        client: PolymarketClient,
        executor: Executor,
        risk: RiskManager,
    ) -> None:
        super().__init__()
        self._client = client
        self._executor = executor
        self._risk = risk
        self._wallets: List[str] = settings.copy_wallet_list
        self._seen_trade_ids: Set[str] = set()
        self._last_prices: Dict[str, float] = {}  # token_id -> price at detection

    async def run(self) -> None:
        if not self._wallets:
            logger.info("Copy trading: sem wallets configuradas — módulo inativo")
            return

        self._running = True
        bot_state.add_log(f"Copy Trading iniciado · {len(self._wallets)} wallets")

        while self._running:
            try:
                await self._poll_cycle()
            except Exception as e:
                logger.error(f"CopyTrading poll erro: {e}")
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_cycle(self) -> None:
        for wallet in self._wallets:
            await self._check_wallet(wallet)

    async def _check_wallet(self, wallet: str) -> None:
        trades = await self._client.get_trades(wallet, limit=20)
        if not trades:
            return

        for trade in trades:
            trade_id = trade.get("id", trade.get("transactionHash", ""))
            if not trade_id or trade_id in self._seen_trade_ids:
                continue

            # Mark as seen immediately to avoid duplicates
            self._seen_trade_ids.add(trade_id)

            # Filter: only copy recent trades (< 60 seconds old)
            ts = trade.get("timestamp", trade.get("createdAt", 0))
            if isinstance(ts, str):
                import datetime
                try:
                    ts = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ts = 0
            if time.time() - float(ts) > 60:
                continue

            # Filter: ignore small trades
            size_usd = float(trade.get("usdcSize", trade.get("size", 0)))
            if size_usd < MIN_TRADE_SIZE:
                continue

            await self._copy_trade(wallet, trade, size_usd)

    async def _copy_trade(
        self, wallet: str, trade: Dict[str, Any], original_size: float
    ) -> None:
        token_id = trade.get("asset", trade.get("tokenId", ""))
        side = trade.get("side", "BUY").upper()
        outcome = trade.get("outcome", "YES").upper()
        original_price = float(trade.get("price", 0.5))
        market_name = trade.get("market", trade.get("slug", token_id[:12]))

        if not token_id:
            return

        # Check current price via orderbook
        book = await self._client.get_orderbook(token_id)
        current_price = original_price
        if book:
            # py-clob-client returns OrderBookSummary object, not dict
            asks = getattr(book, "asks", None) or []
            if asks:
                first_ask = asks[0]
                if isinstance(first_ask, dict):
                    current_price = float(first_ask.get("price", original_price))
                else:
                    current_price = float(getattr(first_ask, "price", original_price))

        # Don't copy if price moved > 3%
        if abs(current_price - original_price) / max(original_price, 0.01) > MAX_PRICE_MOVE:
            logger.debug(f"Copy skip: preço moveu muito {original_price} -> {current_price}")
            return

        # Size with copy ratio
        copy_size = original_size * settings.copy_ratio
        max_by_capital = self._risk._capital * MAX_MARKET_EXPOSURE_PCT
        copy_size = min(copy_size, max_by_capital)
        copy_size = max(1.0, copy_size)

        can, reason = self._risk.can_trade(copy_size)
        if not can:
            logger.debug(f"Copy blocked: {reason}")
            return

        # Execute within 2s target
        await self._executor.place_maker_order(
            token_id=token_id,
            side=outcome,
            price=current_price,
            size=copy_size / current_price,
            market_name=market_name,
        )

        bot_state.add_copy_trade(wallet, market_name, outcome, copy_size)
        bot_state.add_log(
            f"COPY {outcome} {market_name} · ${copy_size:.0f} "
            f"({wallet[:6]}…{wallet[-4:]})"
        )

        logger.info(
            f"Copy trade: {outcome} {market_name} @{current_price:.3f} "
            f"size=${copy_size:.2f} wallet={wallet[:8]}"
        )
