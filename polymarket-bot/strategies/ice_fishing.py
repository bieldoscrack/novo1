"""
Module 3 — Ice Fishing Strategy.

Lógica:
- Coloca ordens limit "deep" a preços extremos (YES/NO a 0.05–0.15)
  em mercados com alta volatilidade ou próximos do vencimento
- Espera pacientemente por panic sells ou spikes de volatilidade
  para preencher ordens a preços irracionais
- Quando preenchidas, as posições têm edge enorme pois foram compradas
  muito abaixo do valor justo
- Similar a ice fishing: fura o buraco no gelo, joga a isca e espera

Filtros de segurança:
- Só entra em mercados com liquidez mínima
- Respeita limites de exposição do RiskManager
- Cancela ordens stale (sem fill após TTL)
- Diversifica em múltiplos mercados para maximizar chances de "pesca"
"""
from __future__ import annotations
import asyncio
import time
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from config import settings
from core.executor import Executor
from core.polymarket_client import PolymarketClient
from core.risk_manager import RiskManager
from core.state_manager import bot_state
from strategies.base import BaseStrategy
from utils.math_utils import implied_prob_from_price, kelly_fraction

# ── Parâmetros da estratégia ──────────────────────────────────────────────────

# Preço máximo para colocar ordem "deep" (compra YES/NO abaixo desse valor)
MAX_ENTRY_PRICE = 0.15
# Preço mínimo (evitar lixo a 0.01)
MIN_ENTRY_PRICE = 0.03
# Distância mínima do preço atual para considerar "deep" (em pontos)
MIN_DEPTH_FROM_MID = 0.25
# Máximo de mercados simultâneos com ordens ativas
MAX_ACTIVE_HOLES = 10
# Tempo de vida de uma ordem antes de cancelar (segundos) — 2 horas
ORDER_TTL = 7200
# Intervalo de scan para buscar novos mercados (segundos)
SCAN_INTERVAL = 120
# Intervalo para verificar ordens ativas (segundos)
CHECK_INTERVAL = 30
# Tamanho máximo por ordem como % do capital
MAX_ORDER_SIZE_PCT = 0.03
# Volume mínimo do mercado para considerar (evita mercados mortos)
MIN_MARKET_VOLUME = 500.0
# Spread mínimo do orderbook para considerar oportunidade
MIN_SPREAD = 0.10


class IceFishingStrategy(BaseStrategy):
    """
    Estratégia "Ice Fishing": coloca ordens limit a preços extremos
    e espera volatilidade/pânico para preencher com edge alto.
    """

    def __init__(
        self,
        client: PolymarketClient,
        executor: Executor,
        risk: RiskManager,
    ) -> None:
        super().__init__()
        self._client = client
        self._executor = executor
        self._risk = risk
        # token_id -> info da "hole" (ordem colocada)
        self._active_holes: Dict[str, _FishingHole] = {}
        # Mercados já avaliados recentemente (cooldown)
        self._cooldown: Dict[str, float] = {}
        self._stats = _FishingStats()

    async def run(self) -> None:
        self._running = True
        bot_state.add_log("Ice Fishing iniciado — procurando buracos no gelo")

        while self._running:
            try:
                # Verificar ordens ativas (fills + TTL)
                await self._check_active_holes()
                # Buscar novos mercados para "pescar"
                await self._scan_for_holes()
            except Exception as e:
                logger.error(f"IceFishing erro: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    # ── Scan: procurar mercados com oportunidade ──────────────────────────────

    async def _scan_for_holes(self) -> None:
        if self._risk.is_paused:
            return

        if len(self._active_holes) >= MAX_ACTIVE_HOLES:
            return

        # Buscar mercados ativos
        markets = await self._client.get_markets(limit=50)
        if not markets:
            return

        now = time.time()

        for market in markets:
            if len(self._active_holes) >= MAX_ACTIVE_HOLES:
                break

            market_id = market.get("conditionId", market.get("id", ""))
            if not market_id:
                continue

            # Cooldown: não reavaliar o mesmo mercado em 10 min
            if now - self._cooldown.get(market_id, 0) < 600:
                continue
            self._cooldown[market_id] = now

            # Filtrar mercados sem volume
            volume = float(market.get("volume", market.get("volumeNum", 0)))
            if volume < MIN_MARKET_VOLUME:
                continue

            # Já temos hole nesse mercado?
            if market_id in self._active_holes:
                continue

            await self._evaluate_market(market, market_id)

    async def _evaluate_market(self, market: Dict[str, Any], market_id: str) -> None:
        """Avalia se vale a pena colocar uma ordem deep nesse mercado."""
        slug = market.get("slug", market.get("question", market_id[:16]))
        market_name = slug[:24] if slug else market_id[:12]

        # Pegar orderbook
        tokens = market.get("tokens", market.get("clobTokenIds", []))
        if not tokens:
            return

        # Tentar o primeiro token (YES)
        token_id = tokens[0] if isinstance(tokens[0], str) else tokens[0].get("token_id", "")
        if not token_id:
            return

        book = await self._client.get_orderbook(token_id)
        if not book:
            return

        bids = book.get("bids", [])
        asks = book.get("asks", [])

        if not bids or not asks:
            return

        best_bid = float(bids[0].get("price", 0))
        best_ask = float(asks[0].get("price", 1))
        spread = best_ask - best_bid

        # Precisa ter spread suficiente (mercado com incerteza)
        if spread < MIN_SPREAD:
            return

        mid_price = (best_bid + best_ask) / 2.0

        # Determinar lado e preço para a ordem deep
        fishing_side, fishing_price = self._pick_fishing_level(
            mid_price, best_bid, best_ask, bids, asks
        )
        if not fishing_side or fishing_price <= 0:
            return

        # Verificar profundidade mínima
        if abs(mid_price - fishing_price) < MIN_DEPTH_FROM_MID:
            return

        # Calcular tamanho
        capital = self._risk._capital
        max_size = capital * MAX_ORDER_SIZE_PCT
        size_usd = min(max_size, capital * 0.02)
        size_usd = max(1.0, size_usd)

        can, reason = self._risk.can_trade(size_usd)
        if not can:
            logger.debug(f"IceFishing blocked: {reason}")
            return

        qty = size_usd / fishing_price

        # Colocar ordem
        order = await self._executor.place_maker_order(
            token_id=token_id,
            side=fishing_side,
            price=fishing_price,
            size=qty,
            market_name=market_name,
        )

        if order:
            self._active_holes[market_id] = _FishingHole(
                market_id=market_id,
                market_name=market_name,
                token_id=token_id,
                order_id=order.order_id,
                side=fishing_side,
                price=fishing_price,
                size_usd=size_usd,
                qty=qty,
                placed_at=time.time(),
            )
            self._stats.holes_drilled += 1
            bot_state.add_log(
                f"ICE FISHING: isca em {market_name} · "
                f"{fishing_side} @{fishing_price:.3f} · ${size_usd:.0f}"
            )
            logger.info(
                f"IceFishing hole: {market_name} {fishing_side} "
                f"@{fishing_price:.3f} qty={qty:.0f} mid={mid_price:.3f}"
            )

    def _pick_fishing_level(
        self,
        mid_price: float,
        best_bid: float,
        best_ask: float,
        bids: List[Dict],
        asks: List[Dict],
    ) -> tuple:
        """
        Determina o lado (YES/NO) e preço para a ordem deep.
        Prefere o lado mais distante do mid e com menos liquidez.
        """
        # Opção 1: Comprar YES barato (se mid está alto, o YES barato está longe)
        # Opção 2: Comprar NO barato (equivalente a vender YES caro)

        yes_fishing_price = 0.0
        no_fishing_price = 0.0

        # Para YES: queremos comprar bem abaixo do mid
        if mid_price > 0.40:
            # YES está "caro", podemos pescar YES barato
            candidate = max(MIN_ENTRY_PRICE, best_bid * 0.4)
            if candidate <= MAX_ENTRY_PRICE:
                yes_fishing_price = round(candidate, 3)

        # Para NO: se mid < 0.60, NO está "caro" implicitamente
        if mid_price < 0.60:
            # NO está "caro" (1 - mid > 0.40), podemos pescar NO barato
            no_implied = 1.0 - mid_price
            candidate = max(MIN_ENTRY_PRICE, (1.0 - best_ask) * 0.4)
            if candidate <= MAX_ENTRY_PRICE:
                no_fishing_price = round(candidate, 3)

        # Escolher o lado com mais profundidade (mais longe do mid = mais edge)
        yes_depth = mid_price - yes_fishing_price if yes_fishing_price > 0 else 0
        no_depth = (1 - mid_price) - no_fishing_price if no_fishing_price > 0 else 0

        if yes_depth > no_depth and yes_fishing_price > 0:
            return "YES", yes_fishing_price
        elif no_fishing_price > 0:
            return "NO", no_fishing_price
        elif yes_fishing_price > 0:
            return "YES", yes_fishing_price

        return None, 0.0

    # ── Verificar holes ativas ────────────────────────────────────────────────

    async def _check_active_holes(self) -> None:
        """Verifica ordens ativas: detecta fills e cancela ordens stale."""
        now = time.time()
        to_remove: List[str] = []

        for market_id, hole in self._active_holes.items():
            # Verificar se a ordem ainda existe no executor
            order = self._executor.open_orders.get(hole.order_id)

            if order is None:
                # Ordem foi preenchida ou cancelada
                self._stats.fish_caught += 1
                bot_state.add_log(
                    f"ICE FISHING: PEIXE! {hole.market_name} · "
                    f"{hole.side} @{hole.price:.3f} preenchida!"
                )
                logger.info(
                    f"IceFishing CATCH: {hole.market_name} {hole.side} "
                    f"@{hole.price:.3f} filled!"
                )
                to_remove.append(market_id)
                continue

            # Verificar TTL
            age = now - hole.placed_at
            if age > ORDER_TTL:
                # Cancelar ordem velha
                await self._executor.cancel_order(hole.order_id)
                self._stats.holes_expired += 1
                bot_state.add_log(
                    f"ICE FISHING: isca expirou em {hole.market_name} "
                    f"({age/3600:.1f}h)"
                )
                to_remove.append(market_id)
                continue

        for mid in to_remove:
            self._active_holes.pop(mid, None)

        # Atualizar estado no dashboard
        self._update_state()

    def _update_state(self) -> None:
        """Atualiza métricas de ice fishing no bot state."""
        bot_state.update(
            ice_fishing={
                "active_holes": len(self._active_holes),
                "max_holes": MAX_ACTIVE_HOLES,
                "fish_caught": self._stats.fish_caught,
                "holes_drilled": self._stats.holes_drilled,
                "holes_expired": self._stats.holes_expired,
                "holes": [
                    {
                        "market": h.market_name,
                        "side": h.side,
                        "price": h.price,
                        "age_min": int((time.time() - h.placed_at) / 60),
                    }
                    for h in self._active_holes.values()
                ],
            }
        )


# ── Data classes auxiliares ───────────────────────────────────────────────────


class _FishingHole:
    """Representa uma 'hole' — uma ordem deep colocada esperando fill."""

    __slots__ = (
        "market_id", "market_name", "token_id", "order_id",
        "side", "price", "size_usd", "qty", "placed_at",
    )

    def __init__(
        self,
        market_id: str,
        market_name: str,
        token_id: str,
        order_id: str,
        side: str,
        price: float,
        size_usd: float,
        qty: float,
        placed_at: float,
    ) -> None:
        self.market_id = market_id
        self.market_name = market_name
        self.token_id = token_id
        self.order_id = order_id
        self.side = side
        self.price = price
        self.size_usd = size_usd
        self.qty = qty
        self.placed_at = placed_at


class _FishingStats:
    """Estatísticas da estratégia."""

    __slots__ = ("holes_drilled", "fish_caught", "holes_expired")

    def __init__(self) -> None:
        self.holes_drilled: int = 0
        self.fish_caught: int = 0
        self.holes_expired: int = 0
