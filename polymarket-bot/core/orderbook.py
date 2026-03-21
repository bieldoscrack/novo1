"""
CLOB orderbook client — WebSocket with REST API fallback.
Tries WS connection; if unavailable, polls REST API for orderbook data.
"""
from __future__ import annotations
import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional, Set

from loguru import logger

from config import settings
from core.state_manager import bot_state

# URLs to try for WebSocket connection (in order)
WS_URLS = [
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    "wss://ws-subscriptions-clob.polymarket.com/ws/",
    "wss://ws-subscriptions-clob.polymarket.com",
]


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
    def __init__(self, rest_client: Any = None) -> None:
        self._books: Dict[str, OrderbookSnapshot] = {}
        self._subscriptions: Set[str] = set()
        self._running: bool = False
        self._ws: Optional[Any] = None
        self._on_update_cb: Optional[Callable[[str, OrderbookSnapshot], None]] = None
        self._rest_client = rest_client  # PolymarketClient for REST fallback
        self._ws_connected: bool = False

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
        # Try WS first, fall back to REST polling
        ws_failed = await self._try_ws_connect()
        if ws_failed:
            bot_state.add_log("CLOB WS indisponível — usando REST API para orderbook")
            logger.info("CLOB WS unavailable, falling back to REST polling")
            await self._run_rest_polling()

    async def _try_ws_connect(self) -> bool:
        """Try each WS URL. Returns True if all failed."""
        import websockets

        for url in WS_URLS:
            if not self._running:
                return True
            try:
                logger.debug(f"Tentando CLOB WS: {url}")
                async with websockets.connect(
                    url, ping_interval=20, open_timeout=10,
                    additional_headers={"Origin": "https://polymarket.com"},
                ) as ws:
                    self._ws = ws
                    self._ws_connected = True
                    bot_state.add_log(f"CLOB WS conectado ✓")
                    logger.info(f"CLOB WS connected: {url}")

                    # Subscribe to known markets
                    subs = list(self._subscriptions)
                    if subs:
                        msg = json.dumps({"assets_ids": subs, "type": "market"})
                        await ws.send(msg)

                    # Stay in WS loop
                    backoff = 2
                    while self._running:
                        try:
                            async for raw in ws:
                                if not self._running:
                                    return False
                                await self._handle(raw)
                        except asyncio.CancelledError:
                            return False
                        except Exception as e:
                            logger.warning(f"CLOB WS erro: {e}. Reconectando em {backoff}s")
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2, 60)
                    return False
            except asyncio.CancelledError:
                return False
            except Exception as e:
                logger.warning(f"CLOB WS {url} falhou: {e}")
                continue

        return True  # All URLs failed

    async def _run_rest_polling(self) -> None:
        """Fallback: poll REST API for orderbook data every 5 seconds."""
        if not self._rest_client:
            bot_state.add_log("WARN: REST fallback sem client — orderbook indisponível")
            # Just idle so we don't crash
            while self._running:
                await asyncio.sleep(30)
            return

        bot_state.add_log("Orderbook REST polling ativo ✓")
        while self._running:
            try:
                for tid in list(self._subscriptions):
                    if not self._running:
                        break
                    await self._poll_rest_book(tid)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"REST orderbook poll erro: {e}")
                await asyncio.sleep(10)

    async def _poll_rest_book(self, token_id: str) -> None:
        """Fetch orderbook via REST API and update snapshot."""
        book_data = await self._rest_client.get_orderbook(token_id)
        if not book_data:
            return

        if token_id not in self._books:
            self._books[token_id] = OrderbookSnapshot(token_id)

        book = self._books[token_id]
        book.last_update = time.time()

        # Handle both dict and OrderBookSummary object from py-clob-client
        bids_raw = getattr(book_data, "bids", None) or (
            book_data.get("bids", []) if isinstance(book_data, dict) else []
        )
        asks_raw = getattr(book_data, "asks", None) or (
            book_data.get("asks", []) if isinstance(book_data, dict) else []
        )

        book.bids = sorted(
            [self._parse_level(b) for b in bids_raw if self._parse_level(b)],
            key=lambda x: -x["price"],
        )
        book.asks = sorted(
            [self._parse_level(a) for a in asks_raw if self._parse_level(a)],
            key=lambda x: x["price"],
        )

        if self._on_update_cb:
            self._on_update_cb(token_id, book)

    @staticmethod
    def _parse_level(level: Any) -> Optional[Dict[str, float]]:
        """Parse a bid/ask level from dict or object."""
        try:
            if isinstance(level, dict):
                return {"price": float(level["price"]), "size": float(level["size"])}
            return {"price": float(level.price), "size": float(level.size)}
        except (KeyError, AttributeError, ValueError, TypeError):
            return None

    async def _send_subscribe(self, ws: Any, token_id: str) -> None:
        msg = json.dumps({"assets_ids": [token_id], "type": "market"})
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
