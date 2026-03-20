"""
Binance public WebSocket feed — no auth required.
Streams BTC/ETH/SOL real-time trade prices.
"""
from __future__ import annotations
import asyncio
import json
import time
from typing import Callable, Dict, Optional

import websockets
from loguru import logger

from config import settings
from core.state_manager import bot_state

# Symbol map: stream name -> display name
SYMBOLS: Dict[str, str] = {
    "btcusdt": "BTC",
    "ethusdt": "ETH",
    "solusdt": "SOL",
}


class BinanceFeed:
    def __init__(self) -> None:
        self._prices: Dict[str, float] = {"BTC": 0.0, "ETH": 0.0, "SOL": 0.0}
        self._last_update: float = 0.0
        self._running: bool = False
        self._on_price_cb: Optional[Callable[[Dict[str, float]], None]] = None

    def on_price(self, cb: Callable[[Dict[str, float]], None]) -> None:
        self._on_price_cb = cb

    @property
    def prices(self) -> Dict[str, float]:
        return dict(self._prices)

    @property
    def is_stale(self) -> bool:
        return time.time() - self._last_update > 10

    def get_signal(self) -> str:
        """Derive direction signal from latest BTC price movement."""
        # Simplified: could track rolling window; here expose raw for strategy use
        return "SCANNING"

    async def run(self) -> None:
        self._running = True
        url = settings.binance_ws_url
        backoff = 2
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    bot_state.add_log("Binance WS conectado ✓")
                    backoff = 2
                    async for raw in ws:
                        if not self._running:
                            break
                        await self._handle(raw)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Binance WS erro: {e}. Reconectando em {backoff}s")
                bot_state.add_log(f"WARN: Binance WS reconectando em {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Combined stream wraps in {"stream":..., "data":{...}}
        data = msg.get("data", msg)
        event = data.get("e", "")

        if event == "trade":
            sym = data.get("s", "").lower().replace("usdt", "")
            price = float(data.get("p", 0))
            display = SYMBOLS.get(sym + "usdt", sym.upper())
            if display in self._prices:
                self._prices[display] = price
                self._last_update = time.time()
                bot_state.update_binance_prices(dict(self._prices))
                if self._on_price_cb:
                    self._on_price_cb(dict(self._prices))

    def stop(self) -> None:
        self._running = False
