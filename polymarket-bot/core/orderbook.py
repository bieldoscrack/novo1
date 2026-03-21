"""
CLOB WebSocket client — subscribes to market orderbook updates.
"""
from __future__ import annotations
import asyncio
import json
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set

import websockets
from loguru import logger

from config import settings
from core.state_manager import bot_state


class OrderbookSnapshot:
    def __init__(self, token_id: str) -> None:
        self.token_id = token_id
        self.bids: List[Dict[str, float]] = []  # [{"price": x, "size": y}]
        self.asks: List[Dict[str, float]] = []
        self.last_update: float = 0.0

    def best_bid(self) -> Optional[float]:
        return self.bids[0]["price"] if self.bids else None

    def best_ask(self) -> Optional[float]:
        return self.asks[0]["price"] if self.asks else None

    def mid(self) -> Optional[float]:
        b, a = self.best_bid(), self.best_ask()
        if b and a:
            return (b + a) / 2
        return b or a


class ClobOrderbookWS:
    def __init__(self) -> None:
        self._books: Dict[str, OrderbookSnapshot] = {}
        self._subscriptions: Set[str] = set()
        self._running: bool = False
        self._ws: Optional[Any] = None
        self._on_update_cb: Optional[Callable[[str, OrderbookSnapshot], None]] = None

    def on_update(
        self, cb: Callable[[str, OrderbookSnapshot], None]
    ) -> None:
        self._on_update_cb = cb

    def subscribe(self, token_id: str) -> None:
        self._subscriptions.add(token_id)

    def unsubscribe(self, token_id: str) -> None:
        self._subscriptions.discard(token_id)

    def get_book(self, token_id: str) -> Optional[OrderbookSnapshot]:
        return self._books.get(token_id)

    async def run(self) -> None:
        self._running = True
        backoff = 2
        while self._running:
            try:
                async with websockets.connect(
                    settings.clob_ws_url, ping_interval=20
                ) as ws:
                    self._ws = ws
                    bot_state.add_log("CLOB WS conectado ✓")
                    backoff = 2
                    # Subscribe to known markets (batch)
                    subs = list(self._subscriptions)
                    if subs:
                        msg = json.dumps({"assets_ids": subs, "type": "market"})
                        await ws.send(msg)
                    async for raw in ws:
                        if not self._running:
                            break
                        await self._handle(raw)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"CLOB WS erro: {e}. Reconectando em {backoff}s")
                bot_state.add_log(f"WARN: CLOB WS reconectando em {backoff}s")
                self._ws = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _send_subscribe(self, ws: Any, token_id: str) -> None:
        msg = json.dumps({"assets_ids": [token_id], "type": "market"})
        await ws.send(msg)

    async def add_subscription(self, token_id: str) -> None:
        self._subscriptions.add(token_id)
        if self._ws:
            try:
                await self._send_subscribe(self._ws, token_id)
            except Exception:
                pass

    async def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        event_type = msg.get("event_type", msg.get("type", ""))
        asset_id = msg.get("asset_id", msg.get("market", ""))

        if not asset_id:
            return

        if asset_id not in self._books:
            self._books[asset_id] = OrderbookSnapshot(asset_id)

        book = self._books[asset_id]
        book.last_update = time.time()

        if event_type in ("book", "price_change"):
            bids_raw = msg.get("bids", [])
            asks_raw = msg.get("asks", [])
            book.bids = sorted(
                [{"price": float(b["price"]), "size": float(b["size"])} for b in bids_raw],
                key=lambda x: -x["price"],
            )
            book.asks = sorted(
                [{"price": float(a["price"]), "size": float(a["size"])} for a in asks_raw],
                key=lambda x: x["price"],
            )

        if self._on_update_cb:
            self._on_update_cb(asset_id, book)

    def stop(self) -> None:
        self._running = False
