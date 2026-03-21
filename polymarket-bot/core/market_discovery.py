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
            bot_state.add_log("MarketDiscovery: nenhum mercado encontrado")
            return

        # Extract real token IDs from market data
        enriched = []
        for m in markets:
            yes_token, no_token = self._extract_token_ids(m)
            if yes_token:
                m["_yes_token_id"] = yes_token
                m["_no_token_id"] = no_token or ""
                enriched.append(m)

        self._markets = enriched

        # Update dashboard state
        dashboard_markets = [
            {
                "name": self._short_name(m),
                "price": self._mid_price(m),
                "change": 0.0,
                "token_id": m.get("_yes_token_id", ""),
            }
            for m in enriched[:10]
        ]
        bot_state.update(markets=dashboard_markets)
        bot_state.add_log(f"Mercados: {len(enriched)} encontrados com token IDs")
        logger.info(f"MarketDiscovery: {len(enriched)} mercados com tokens válidos")

    def _extract_token_ids(self, m: Dict[str, Any]) -> tuple:
        """Extract YES and NO token IDs from Gamma API market data."""
        # Try 'tokens' array first (preferred)
        tokens = m.get("tokens", [])
        if tokens:
            yes_id = ""
            no_id = ""
            for t in tokens:
                outcome = t.get("outcome", "").upper()
                tid = t.get("token_id", "")
                if outcome == "YES":
                    yes_id = tid
                elif outcome == "NO":
                    no_id = tid
            if yes_id:
                return yes_id, no_id

        # Try 'clobTokenIds' array [YES, NO]
        clob_ids = m.get("clobTokenIds", [])
        if clob_ids and len(clob_ids) >= 1:
            yes_id = clob_ids[0] if len(clob_ids) > 0 else ""
            no_id = clob_ids[1] if len(clob_ids) > 1 else ""
            if yes_id:
                return yes_id, no_id

        # Fallback: use conditionId (may not work for CLOB WS)
        cid = m.get("conditionId", m.get("id", ""))
        if cid:
            return cid, ""

        return "", ""

    def get_market_by_token(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Find a market by its token_id."""
        for m in self._markets:
            if m.get("_yes_token_id") == token_id or m.get("_no_token_id") == token_id:
                return m
        return None

    def _short_name(self, m: Dict[str, Any]) -> str:
        slug = m.get("slug", "")
        q = m.get("question", slug)
        return q[:20] if q else slug[:20]

    def _mid_price(self, m: Dict[str, Any]) -> float:
        # Try tokens array first
        tokens = m.get("tokens", [])
        for t in tokens:
            if t.get("outcome", "").upper() == "YES":
                try:
                    return float(t.get("price", 0.5))
                except (ValueError, TypeError):
                    pass
        # outcomePrices fallback
        prices = m.get("outcomePrices", [])
        if prices:
            try:
                return float(prices[0])
            except (ValueError, IndexError):
                pass
        return 0.5
