"""
Order executor — maker orders, cancel/replace loop, simulation support.
"""
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from config import settings
from core.polymarket_client import PolymarketClient
from core.state_manager import bot_state
from utils.math_utils import pair_cost


@dataclass
class OpenOrder:
    order_id: str
    token_id: str
    side: str  # YES or NO
    price: float
    size: float
    filled: float = 0.0
    created_at: float = field(default_factory=time.time)
    market_name: str = ""


@dataclass
class Position:
    market_name: str
    token_id_yes: str
    token_id_no: str
    qty_yes: float = 0.0
    qty_no: float = 0.0
    cost_yes: float = 0.0
    cost_no: float = 0.0

    @property
    def pair_cost_val(self) -> float:
        return pair_cost(self.qty_yes, self.qty_no, self.cost_yes, self.cost_no)

    @property
    def potential_profit(self) -> float:
        matched = min(self.qty_yes, self.qty_no)
        return round(matched * (1.0 - self.pair_cost_val), 4) if matched > 0 else 0.0


class Executor:
    def __init__(self, client: PolymarketClient) -> None:
        self._client = client
        self._open_orders: Dict[str, OpenOrder] = {}
        self._positions: Dict[str, Position] = {}
        self._sim_order_counter: int = 0

    # ── Order creation ────────────────────────────────────────────────────────

    async def place_maker_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        market_name: str = "",
        fee_rate_bps: int = 0,
    ) -> Optional[OpenOrder]:
        if settings.simulation_mode:
            self._sim_order_counter += 1
            oid = f"SIM-{self._sim_order_counter:06d}"
            order = OpenOrder(
                order_id=oid,
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                market_name=market_name,
            )
            self._open_orders[oid] = order
            bot_state.add_log(
                f"SIM BUY {side} {market_name} @${price:.3f} · {size:.0f} shares"
            )
            # Simulate instant fill in sim mode
            await asyncio.sleep(0.01)
            await self._handle_fill(order, size)
            return order

        result = await self._client.create_order(
            token_id=token_id,
            price=price,
            size=size,
            side="BUY",
            fee_rate_bps=fee_rate_bps,
        )
        if not result:
            return None

        order_id = result.get("orderID", result.get("id", f"ORD-{time.time():.0f}"))
        order = OpenOrder(
            order_id=order_id,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            market_name=market_name,
        )
        self._open_orders[order_id] = order
        bot_state.add_log(
            f"BUY {side} {market_name} @${price:.3f} · {size:.0f} shares"
        )
        return order

    # ── Cancel ────────────────────────────────────────────────────────────────

    async def cancel_order(self, order_id: str) -> bool:
        ok = await self._client.cancel_order(order_id)
        if ok:
            self._open_orders.pop(order_id, None)
        return ok

    async def cancel_all(self) -> None:
        for oid in list(self._open_orders.keys()):
            await self.cancel_order(oid)

    # ── Fill simulation ───────────────────────────────────────────────────────

    async def _handle_fill(self, order: OpenOrder, qty: float) -> None:
        order.filled += qty
        mid = order.market_name
        key = order.token_id

        if mid not in self._positions:
            self._positions[mid] = Position(
                market_name=mid,
                token_id_yes=key if order.side == "YES" else "",
                token_id_no=key if order.side == "NO" else "",
            )

        pos = self._positions[mid]
        if order.side == "YES":
            if not pos.token_id_yes:
                pos.token_id_yes = key
            pos.qty_yes += qty
            pos.cost_yes += qty * order.price
        else:
            if not pos.token_id_no:
                pos.token_id_no = key
            pos.qty_no += qty
            pos.cost_no += qty * order.price

        self._sync_positions_to_state()

    def _sync_positions_to_state(self) -> None:
        positions = [
            {
                "market": p.market_name,
                "qty_yes": round(p.qty_yes, 2),
                "qty_no": round(p.qty_no, 2),
                "pair_cost": round(p.pair_cost_val, 4),
                "potential_profit": p.potential_profit,
            }
            for p in self._positions.values()
            if p.qty_yes > 0 or p.qty_no > 0
        ]
        bot_state.update(
            positions=positions,
            active_bets=len(positions),
        )

    # ── Resolution ────────────────────────────────────────────────────────────

    async def resolve_market(
        self, market_name: str, outcome: str  # "YES" or "NO"
    ) -> float:
        pos = self._positions.pop(market_name, None)
        if not pos:
            return 0.0
        if outcome == "YES":
            pnl = pos.qty_yes * 1.0 - pos.cost_yes
        else:
            pnl = pos.qty_no * 1.0 - pos.cost_no
        pnl = round(pnl, 4)
        bot_state.add_resolved(market_name, outcome, pnl)
        bot_state.add_log(
            f"RESOLVED {market_name} → {outcome} · "
            f"{'+'if pnl>=0 else ''}{pnl:.2f}"
        )
        self._sync_positions_to_state()
        return pnl

    @property
    def positions(self) -> Dict[str, Position]:
        return self._positions

    @property
    def open_orders(self) -> Dict[str, OpenOrder]:
        return self._open_orders
