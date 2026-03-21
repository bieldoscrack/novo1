"""
Entry point: starts the trading bot and API server concurrently.

Usage:
    python main.py

The FastAPI server runs at http://127.0.0.1:8080
SIMULATION_MODE=true by default — no real trades until explicitly disabled.
"""
from __future__ import annotations
import asyncio
import signal
import sys
import time
from typing import List

import uvicorn
from loguru import logger

from config import settings
from core.binance_feed import BinanceFeed
from core.database import init_db
from core.executor import Executor
from core.market_discovery import MarketDiscovery
from core.orderbook import ClobOrderbookWS
from core.polymarket_client import PolymarketClient
from core.risk_manager import RiskManager
from core.state_manager import bot_state
from server import app
from strategies.copy_trading import CopyTradingStrategy
from strategies.pair_trading import PairTradingStrategy
from utils.allowance_checker import check_allowances
from utils.telegram_alerts import TelegramAlerter

# Configure loguru
logger.remove()
logger.add(
    sys.stderr,
    level=settings.log_level,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
)
logger.add(
    "data/bot.log",
    level="DEBUG",
    rotation="10 MB",
    retention="7 days",
    compression="gz",
)


async def resolution_monitor(
    client: PolymarketClient, executor: Executor
) -> None:
    """Poll Gamma API to detect real market outcomes and resolve paper positions."""
    bot_state.add_log("Resolution monitor ativo ✓ — rastreando resultados reais")

    while True:
        try:
            await asyncio.sleep(30)

            # Check all open positions for resolution
            positions = dict(executor.positions)
            if not positions:
                continue

            for market_name, pos in positions.items():
                meta = executor.market_meta.get(market_name, {})
                slug = meta.get("slug", "")
                if not slug:
                    continue

                # Poll Gamma API for this market
                market_data = await client.get_market_by_slug(slug)
                if not market_data:
                    continue

                # Check if market has resolved (closed with clear outcome)
                if not market_data.get("closed", False):
                    continue

                # Determine outcome from outcomePrices
                # When resolved: YES wins → outcomePrices=["1"] or ["1","0"]
                #                NO wins  → outcomePrices=["0"] or ["0","1"]
                outcome_prices = market_data.get("outcomePrices", [])
                if not outcome_prices:
                    continue

                try:
                    yes_price = float(outcome_prices[0])
                except (ValueError, IndexError):
                    continue

                # Only resolve if price is clearly 0 or 1 (market fully resolved)
                if yes_price >= 0.95:
                    outcome = "YES"
                elif yes_price <= 0.05:
                    outcome = "NO"
                else:
                    # Not yet fully resolved
                    continue

                pnl = await executor.resolve_market(market_name, outcome)
                result = "WIN" if pnl >= 0 else "LOSS"
                bot_state.add_log(
                    f"📊 {result}: {market_name} → {outcome} "
                    f"({'+'if pnl>=0 else ''}{pnl:.2f} USDC)"
                )
                logger.info(
                    f"Resolution: {market_name} → {outcome} | "
                    f"PnL={pnl:+.2f} | {result}"
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"ResolutionMonitor erro: {e}")
            await asyncio.sleep(10)


async def run_bot(
    client: PolymarketClient,
    binance: BinanceFeed,
    ob_ws: ClobOrderbookWS,
    discovery: MarketDiscovery,
    executor: Executor,
    risk: RiskManager,
    telegram: TelegramAlerter,
) -> None:
    """Core bot loop — runs all strategies concurrently."""
    pair_strategy = PairTradingStrategy(binance, ob_ws, discovery, executor, risk)
    copy_strategy = CopyTradingStrategy(client, executor, risk)

    # Daily P&L alert at midnight UTC
    async def daily_alert_loop() -> None:
        while True:
            now = time.gmtime()
            # Sleep until next midnight
            secs_until_midnight = 86400 - (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec)
            await asyncio.sleep(secs_until_midnight)
            state = bot_state.get_state()
            await telegram.pnl_daily(
                pnl=state.get("pnl", 0),
                wins=state.get("wins", 0),
                losses=state.get("losses", 0),
            )

    tasks = [
        asyncio.create_task(binance.run(), name="binance-feed"),
        asyncio.create_task(ob_ws.run(), name="clob-ws"),
        asyncio.create_task(discovery.run(), name="market-discovery"),
        asyncio.create_task(pair_strategy.run(), name="pair-trading"),
        asyncio.create_task(copy_strategy.run(), name="copy-trading"),
        asyncio.create_task(daily_alert_loop(), name="daily-alert"),
        asyncio.create_task(
            resolution_monitor(client, executor), name="resolution-monitor"
        ),
    ]

    bot_state.add_log(
        "Bot iniciado · PAPER TRADING (dados reais, ordens simuladas)"
        if settings.simulation_mode
        else "Bot iniciado · LIVE TRADING"
    )
    bot_state.update(simulation_mode=settings.simulation_mode)

    logger.info(
        f"Bot rodando | simulation={settings.simulation_mode} | "
        f"port={settings.dashboard_port}"
    )

    # Set initial balance
    bot_state.update(balance=settings.initial_capital)

    # Copy trading: sem wallets configuradas — módulo inativo
    if not settings.copy_wallets.strip():
        bot_state.add_log("Copy trading: sem wallets configuradas — módulo inativo")

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Bot cancelado — shutdown gracioso")
        for t in tasks:
            t.cancel()
        # Cancel open orders before exit
        await executor.cancel_all()
        await client.close()


async def main() -> None:
    # ── Init ──────────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("  POLYMARKET BOT — INICIANDO")
    logger.info(f"  Simulação: {settings.simulation_mode}")
    logger.info(f"  Dashboard: http://127.0.0.1:{settings.dashboard_port}")
    logger.info("=" * 60)

    if not settings.simulation_mode and not settings.has_wallet:
        logger.error("LIVE mode requer PRIVATE_KEY no .env")
        sys.exit(1)

    # DB
    await init_db()

    # Components
    client = PolymarketClient()
    await client.async_init_clob()

    binance = BinanceFeed()
    ob_ws = ClobOrderbookWS(rest_client=client)
    discovery = MarketDiscovery(client)
    executor = Executor(client)
    risk = RiskManager()
    telegram = TelegramAlerter()

    await telegram.init()

    # Allowance check (non-blocking warning)
    if settings.has_wallet and not settings.simulation_mode:
        from eth_account import Account
        try:
            acct = Account.from_key(settings.private_key)
            allowances = await check_allowances(acct.address)
            if allowances and not all(allowances.values()):
                missing = [k for k, v in allowances.items() if not v]
                bot_state.add_log(f"WARN: allowances ausentes: {missing}")
        except Exception:
            pass

    # ── Graceful shutdown ────────────────────────────────────────────────────
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("Sinal de shutdown recebido")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass  # Windows

    # ── Uvicorn server ───────────────────────────────────────────────────────
    uvicorn_config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=settings.dashboard_port,
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(uvicorn_config)

    # Run both concurrently
    bot_task = asyncio.create_task(
        run_bot(client, binance, ob_ws, discovery, executor, risk, telegram),
        name="bot-main",
    )
    server_task = asyncio.create_task(server.serve(), name="api-server")

    try:
        done, pending = await asyncio.wait(
            [bot_task, server_task, asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        logger.info("Encerrando componentes…")
        bot_task.cancel()
        server.should_exit = True
        for t in [bot_task, server_task]:
            try:
                await asyncio.wait_for(t, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        await client.close()
        logger.info("Bot encerrado.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
