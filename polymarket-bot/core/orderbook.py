"""
CLOB WebSocket client — subscribes to market orderbook updates.
Falls back to REST API polling when WebSocket is unavailable.
"""
from __future__ import annotations
import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional, Set

import httpx
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
        self._ws_connected: bool = False
        self._on_update_cb: Optional[Callable[[str, OrderbookSnapshot], None]] = None
        self._http = httpx.AsyncClient(timeout=8)

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
        # Run WS loop and REST polling concurrently
        rest_task = asyncio.create_task(self._poll_rest_loop())
        try:
            await self._ws_loop()
        finally:
            rest_task.cancel()
            try:
                await rest_task
            except asyncio.CancelledError:
                pass
            await self._http.aclose()

    async def _ws_loop(self) -> None:
        backoff = 2
        while self._running:
            try:
                async with websockets.connect(
                    settings.clob_ws_url, ping_interval=20
                ) as ws:
                    self._ws = ws
                    self._ws_connected = True
                    bot_state.add_log("CLOB WS conectado ✓")
                    backoff = 2
                    # Subscribe to known markets
                    for tid in list(self._subscriptions):
                        await self._send_subscribe(ws, tid)
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
                self._ws_connected = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _poll_rest_loop(self) -> None:
        """REST fallback: polls orderbooks via HTTP when WS is not connected."""
        await asyncio.sleep(5)  # Give WS a chance to connect first
        while self._running:
            if not self._ws_connected and self._subscriptions:
                polled = 0
                for token_id in list(self._subscriptions):
                    try:
                        r = await self._http.get(
                            f"{settings.clob_api_url}/book",
                            params={"token_id": token_id},
                        )
                        if r.status_code == 200:
                            data = r.json()
                            await self._handle_rest(token_id, data)
                            polled += 1
                    except asyncio.CancelledError:
                        return
                    except Exception as e:
                        logger.debug(f"REST orderbook erro para {token_id[:12]}: {e}")
                if polled > 0:
                    logger.debug(f"REST orderbook: {polled} mercados atualizados")
                await asyncio.sleep(4)
            else:
                await asyncio.sleep(2)

    async def _handle_rest(self, token_id: str, data: dict) -> None:
        """Parse REST API orderbook response and update snapshot."""
        if token_id not in self._books:
            self._books[token_id] = OrderbookSnapshot(token_id)
        book = self._books[token_id]
        book.last_update = time.time()

        bids_raw = data.get("bids", [])
        asks_raw = data.get("asks", [])
        try:
            book.bids = sorted(
                [{"price": float(b["price"]), "size": float(b["size"])} for b in bids_raw],
                key=lambda x: -x["price"],
            )
            book.asks = sorted(
                [{"price": float(a["price"]), "size": float(a["size"])} for a in asks_raw],
                key=lambda x: x["price"],
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"REST orderbook parse erro: {e}")
            return

        if self._on_update_cb:
            self._on_update_cb(token_id, book)

    async def _send_subscribe(self, ws: Any, token_id: str) -> None:
        msg = json.dumps({"type": "subscribe", "market": token_id, "channel": "market"})
        await ws.send(msg)

    async def add_subscription(self, token_id: str) -> None:
        self._subscriptions.add(token_id)
        if self._ws and self._ws_connected:
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
