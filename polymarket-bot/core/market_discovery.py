"""
Discovers active crypto 5m/15m markets via Gamma API.
Runs on a polling loop and updates bot state.
"""
from __future__ import annotations
import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from config import settings
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
        if settings.simulation_mode:
            await self._discover_simulation()
            return
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

    async def _discover_simulation(self) -> None:
        """Generate fake crypto markets for simulation mode."""
        import random
        import time as _time
        counter = int(_time.time()) % 1000
        fake_markets = []
        for sym, name in [("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("SOL", "Solana")]:
            for period in ["5m", "15m"]:
                mid = round(random.uniform(0.40, 0.60), 2)
                mkt = {
                    "slug": f"{sym.lower()}-{period}-{counter}",
                    "question": f"Will {name} go UP in next {period}? #{counter}",
                    "conditionId": f"sim-{sym}-{period}-{counter}",
                    "id": f"sim-{sym}-{period}-{counter}",
                    "outcomePrices": [str(mid)],
                    "closed": False,
                }
                fake_markets.append(mkt)
                counter += 1

        self._markets = fake_markets
        dashboard_markets = [
            {
                "name": f"{m['question'][:22]}",
                "price": self._mid_price(m),
                "change": round(random.uniform(-3.0, 3.0), 1),
                "token_id": m.get("conditionId", ""),
            }
            for m in fake_markets
        ]
        bot_state.update(markets=dashboard_markets)
        logger.debug(f"MarketDiscovery SIM: {len(fake_markets)} mercados gerados")

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
