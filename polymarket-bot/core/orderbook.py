"""
CLOB orderbook client — REST API polling with optional WebSocket supplement.
Always polls REST for reliable book data; WS provides faster updates when available.
"""
from __future__ import annotations
import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional, Set

from loguru import logger

from config import settings
from core.state_manager import bot_state

WS_URLS = [
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    "wss://ws-subscriptions-clob.polymarket.com/ws/",
]


class OrderbookSnapshot:
    def __init__(self, token_id: str) -> None:
        self.token_id = token_id
        self.bids: List[Dict[str, float]] = []
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
        self._rest_client = rest_client
        self._failed_tokens: Set[str] = set()  # Track tokens that 404

    def on_update(self, cb: Callable[[str, OrderbookSnapshot], None]) -> None:
        self._on_update_cb = cb

    def subscribe(self, token_id: str) -> None:
        self._subscriptions.add(token_id)

    def unsubscribe(self, token_id: str) -> None:
        self._subscriptions.discard(token_id)

    def get_book(self, token_id: str) -> Optional[OrderbookSnapshot]:
        return self._books.get(token_id)

    async def run(self) -> None:
        """Run REST polling (primary) + WS (optional supplement)."""
        self._running = True

        # Start WS in background (best-effort, non-blocking)
        ws_task = asyncio.create_task(self._ws_loop(), name="clob-ws-bg")

        # REST polling is the primary data source
        await self._run_rest_polling()

        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass

    async def _ws_loop(self) -> None:
        """Background WS connection for real-time updates."""
        import websockets

        await asyncio.sleep(2)  # Let REST poll first

        for url in WS_URLS:
            if not self._running:
                return
            try:
                async with websockets.connect(
                    url, ping_interval=20, open_timeout=10,
                    additional_headers={"Origin": "https://polymarket.com"},
                ) as ws:
                    self._ws = ws
                    bot_state.add_log("CLOB WS conectado ✓ (suplementar)")
                    logger.info(f"CLOB WS connected: {url}")

                    subs = list(self._subscriptions)
                    if subs:
                        await ws.send(json.dumps({"assets_ids": subs, "type": "market"}))

                    async for raw in ws:
                        if not self._running:
                            return
                        await self._handle(raw)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"CLOB WS {url}: {e}")
                continue

    async def _run_rest_polling(self) -> None:
        """Poll REST API for orderbook data every 5 seconds."""
        if not self._rest_client:
            bot_state.add_log("WARN: Sem client REST — orderbook indisponível")
            while self._running:
                await asyncio.sleep(30)
            return

        bot_state.add_log("Orderbook REST polling ativo ✓")
        poll_count = 0

        while self._running:
            try:
                subs = list(self._subscriptions)
                updated = 0
                failed = 0
                for tid in subs:
                    if not self._running:
                        break
                    # Skip tokens that already 404'd
                    if tid in self._failed_tokens:
                        failed += 1
                        continue
                    # Skip if WS already gave us fresh data (< 10s old)
                    existing = self._books.get(tid)
                    if existing and (time.time() - existing.last_update) < 10:
                        updated += 1
                        continue
                    ok = await self._poll_rest_book(tid)
                    if ok:
                        updated += 1
                        if poll_count < 3:
                            book = self._books.get(tid)
                            bid = book.best_bid() if book else None
                            ask = book.best_ask() if book else None
                            bot_state.add_log(
                                f"Book OK: {tid[:12]}... bid={bid} ask={ask}"
                            )

                poll_count += 1
                # Log every poll for first 3, then every ~60s
                if poll_count <= 3 or poll_count % 12 == 1:
                    bot_state.add_log(
                        f"Orderbook: {updated}/{len(subs)} tokens com dados"
                        + (f", {failed} inválidos" if failed else "")
                    )

                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"REST orderbook poll erro: {e}")
                await asyncio.sleep(10)

    async def _poll_rest_book(self, token_id: str) -> bool:
        """Fetch orderbook via REST API and update snapshot."""
        try:
            book_data = await self._rest_client.get_orderbook(token_id)
        except Exception as e:
            err_str = str(e)
            if "404" in err_str or "No orderbook" in err_str:
                self._failed_tokens.add(token_id)
                bot_state.add_log(
                    f"Token inválido (404): {token_id[:16]}... — removido do polling"
                )
                logger.warning(f"Token {token_id[:20]}... has no orderbook, removing")
            return False
        if not book_data:
            return False

        if token_id not in self._books:
            self._books[token_id] = OrderbookSnapshot(token_id)

        book = self._books[token_id]

        # Handle both dict and OrderBookSummary object
        bids_raw = getattr(book_data, "bids", None)
        if bids_raw is None and isinstance(book_data, dict):
            bids_raw = book_data.get("bids", [])
        asks_raw = getattr(book_data, "asks", None)
        if asks_raw is None and isinstance(book_data, dict):
            asks_raw = book_data.get("asks", [])

        if not bids_raw and not asks_raw:
            return False

        parsed_bids = [self._parse_level(b) for b in (bids_raw or [])]
        parsed_asks = [self._parse_level(a) for a in (asks_raw or [])]

        book.bids = sorted(
            [b for b in parsed_bids if b],
            key=lambda x: -x["price"],
        )
        book.asks = sorted(
            [a for a in parsed_asks if a],
            key=lambda x: x["price"],
        )
        book.last_update = time.time()

        if self._on_update_cb:
            self._on_update_cb(token_id, book)

        return bool(book.bids or book.asks)

    @staticmethod
    def _parse_level(level: Any) -> Optional[Dict[str, float]]:
        try:
            if isinstance(level, dict):
                return {"price": float(level["price"]), "size": float(level["size"])}
            return {"price": float(level.price), "size": float(level.size)}
        except (KeyError, AttributeError, ValueError, TypeError):
            return None

    async def add_subscription(self, token_id: str) -> None:
        self._subscriptions.add(token_id)
        if self._ws:
            try:
                msg = json.dumps({"assets_ids": [token_id], "type": "market"})
                await self._ws.send(msg)
            except Exception:
                pass

    async def _handle(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        messages = data if isinstance(data, list) else [data]
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            self._handle_msg(msg)

    def _handle_msg(self, msg: Dict[str, Any]) -> None:
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
