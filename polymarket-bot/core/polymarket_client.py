"""
Wrapper around py-clob-client + Gamma API + Data API.
Handles auth setup and common operations.
"""
from __future__ import annotations
import asyncio
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from config import settings
from core.state_manager import bot_state


class PolymarketClient:
    def __init__(self) -> None:
        self._clob: Optional[Any] = None  # ClobClient lazy-loaded
        self._creds: Optional[Any] = None
        self._http = httpx.AsyncClient(timeout=10)

    # ── CLOB init ─────────────────────────────────────────────────────────────

    def init_clob(self) -> None:
        """Sync init — call once at startup from async context via run_in_executor."""
        if not settings.has_wallet:
            logger.warning("PRIVATE_KEY não configurada — modo sem trading ativo")
            return
        try:
            from py_clob_client.client import ClobClient
            self._clob = ClobClient(
                settings.clob_api_url,
                key=settings.private_key,
                chain_id=settings.chain_id,
                signature_type=settings.signature_type,
                funder=settings.funder_address or None,
            )
            self._creds = self._clob.create_or_derive_api_creds()
            self._clob.set_api_creds(self._creds)
            logger.info("ClobClient inicializado ✓")
            bot_state.add_log("ClobClient inicializado ✓")
        except ImportError:
            logger.warning("py-clob-client não instalado — CLOB indisponível")
        except Exception as e:
            logger.error(f"Erro ao inicializar ClobClient: {e}")
            bot_state.add_log(f"WARN: ClobClient erro: {e}")

    async def async_init_clob(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.init_clob)

    # ── Gamma API ─────────────────────────────────────────────────────────────

    async def get_active_markets(
        self, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        url = f"{settings.gamma_api_url}/markets"
        params = {"closed": "false", "limit": limit, "offset": offset}
        try:
            r = await self._http.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                return data
            return data.get("markets", [])
        except Exception as e:
            logger.warning(f"Gamma API erro: {e}")
            return []

    async def get_crypto_markets(self) -> List[Dict[str, Any]]:
        """Fetch BTC 5-minute up/down markets."""
        url = f"{settings.gamma_api_url}/markets"
        params = {"closed": "false", "limit": 100}
        try:
            r = await self._http.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            markets = data if isinstance(data, list) else data.get("markets", [])
            # Focus on BTC 5-minute markets
            btc_keywords = ["btc", "bitcoin"]
            result = []
            for m in markets:
                combined = (m.get("slug", "") + m.get("question", "")).lower()
                if m.get("closed", True):
                    continue
                if not any(k in combined for k in btc_keywords):
                    continue
                # Prefer 5-minute markets but include all BTC
                result.append(m)
            return result
        except Exception as e:
            logger.warning(f"Gamma crypto markets erro: {e}")
            return []

    async def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        url = f"{settings.gamma_api_url}/markets"
        try:
            r = await self._http.get(url, params={"slug": slug})
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                return data[0] if data else None
            return data
        except Exception as e:
            logger.warning(f"get_market_by_slug erro: {e}")
            return None

    # ── Data API ──────────────────────────────────────────────────────────────

    async def get_positions(self, address: str) -> List[Dict[str, Any]]:
        url = f"{settings.data_api_url}/positions"
        try:
            r = await self._http.get(url, params={"user": address})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"Data API positions erro: {e}")
            return []

    async def get_trades(
        self, address: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        url = f"{settings.data_api_url}/trades"
        try:
            r = await self._http.get(url, params={"user": address, "limit": limit})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"Data API trades erro: {e}")
            return []

    # ── CLOB order ops ────────────────────────────────────────────────────────

    async def get_orderbook(self, token_id: str) -> Optional[Dict[str, Any]]:
        if not self._clob:
            return None
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, lambda: self._clob.get_order_book(token_id)
            )
        except Exception as e:
            logger.warning(f"get_orderbook erro: {e}")
            return None

    async def create_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,  # "BUY" or "SELL"
        fee_rate_bps: int = 0,
    ) -> Optional[Dict[str, Any]]:
        if not self._clob or settings.simulation_mode:
            logger.debug(f"SIM create_order {side} {size}@{price} token={token_id}")
            return {"simulation": True, "side": side, "price": price, "size": size}
        loop = asyncio.get_event_loop()
        try:
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY, SELL
            args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY if side == "BUY" else SELL,
                fee_rate_bps=fee_rate_bps,
            )
            return await loop.run_in_executor(
                None, lambda: self._clob.create_and_post_order(args)
            )
        except Exception as e:
            logger.error(f"create_order erro: {e}")
            bot_state.add_log(f"ERR: create_order falhou: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        if not self._clob or settings.simulation_mode:
            return True
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, lambda: self._clob.cancel(order_id)
            )
            return True
        except Exception as e:
            logger.warning(f"cancel_order erro: {e}")
            return False

    async def close(self) -> None:
        await self._http.aclose()
