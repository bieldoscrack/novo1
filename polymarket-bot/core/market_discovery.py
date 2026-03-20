"""
Discovers active crypto 5m/15m markets via Gamma API.
Runs on a polling loop and updates bot state.
"""
from __future__ import annotations
import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from core.polymarket_client import PolymarketClient
from core.state_manager import bot_state


class MarketDiscovery:
    def __init__(self, client: PolymarketClient) -> None:
        self._client = client
        self._markets: List[Dict[str, Any]] = []
        self._running: bool = False
        self._poll_interval: int = 60  # seconds

    @property
    def markets(self) -> List[Dict[str, Any]]:
        return list(self._markets)

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._discover()
            except Exception as e:
                logger.warning(f"MarketDiscovery erro: {e}")
            await asyncio.sleep(self._poll_interval)

    async def _discover(self) -> None:
        markets = await self._client.get_crypto_markets()
        if not markets:
            return

        self._markets = markets

        # Update dashboard state
        dashboard_markets = [
            {
                "name": self._short_name(m),
                "price": self._mid_price(m),
                "change": 0.0,
                "token_id": m.get("conditionId", m.get("id", "")),
            }
            for m in markets[:10]
        ]
        bot_state.update(markets=dashboard_markets)
        logger.debug(f"MarketDiscovery: {len(markets)} mercados encontrados")

    def _short_name(self, m: Dict[str, Any]) -> str:
        slug = m.get("slug", "")
        q = m.get("question", slug)
        # Take first 20 chars
        return q[:20] if q else slug[:20]

    def _mid_price(self, m: Dict[str, Any]) -> float:
        # outcomePrices is often available in Gamma response
        prices = m.get("outcomePrices", [])
        if prices:
            try:
                return float(prices[0])
            except (ValueError, IndexError):
                pass
        return 0.5
