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


async def simulation_trade_loop(executor: Executor) -> None:
    """Generate realistic fake trades in simulation mode."""
    import random
    symbols = ["BTC", "ETH", "SOL"]
    periods = ["5m", "15m"]
    market_counter = 800
    trade_count = 0

    bot_state.add_log("Motor de simulação ativo ✓")

    while True:
        try:
            # Wait between 15-45 seconds between trade cycles
            await asyncio.sleep(15 + random.random() * 30)

            sym = random.choice(symbols)
            period = random.choice(periods)
            market_counter += 1
            market_name = f"{sym}-{period} #{market_counter}"

            # Simulate pair trade
            yes_price = round(0.40 + random.random() * 0.18, 3)
            no_price = round(1.0 - yes_price - random.uniform(0.01, 0.04), 3)
            qty = round(30 + random.random() * 80, 0)

            token_id = f"sim-{sym.lower()}-{market_counter}"

            # Buy YES
            await executor.place_maker_order(
                token_id=token_id,
                side="YES",
                price=yes_price,
                size=qty,
                market_name=market_name,
            )

            await asyncio.sleep(1 + random.random() * 3)

            # Buy NO
            await executor.place_maker_order(
                token_id=token_id + "-no",
                side="NO",
                price=no_price,
                size=qty,
                market_name=market_name,
            )

            pc = (yes_price + no_price)
            bot_state.add_log(f"pair_cost = ${pc:.3f} {'✓' if pc < 0.98 else '✗'}")

            # Wait for "resolution"
            resolve_delay = 20 + random.random() * 40
            await asyncio.sleep(resolve_delay)

            # Resolve market
            outcome = random.choice(["YES", "NO"])
            pnl = round((1.0 - pc) * qty * (0.6 + random.random() * 0.8), 2)
            # Occasionally lose
            if random.random() < 0.15:
                pnl = round(-pnl * 0.3, 2)

            await executor.resolve_market(market_name, outcome)
            trade_count += 1

            # Update balance
            state = bot_state.get_state()
            bot_state.update(balance=round(state.get("balance", 500.0), 2))

            # Occasional copy trade simulation
            if random.random() < 0.3:
                fake_wallets = [
                    "0xa1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
                    "0xf1e2d3c4b5a6f7e8d9c0b1a2f3e4d5c6b7a8f9e0",
                    "0x1234567890abcdef1234567890abcdef12345678",
                ]
                copy_markets = ["Trump 2028", "ETH >4k", "BTC >100k", "FIFA WC", "Fed Rate Cut"]
                bot_state.add_copy_trade(
                    wallet=random.choice(fake_wallets),
                    market=random.choice(copy_markets),
                    side=random.choice(["YES", "NO"]),
                    amount=round(10 + random.random() * 40, 2),
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"SimLoop erro: {e}")
            await asyncio.sleep(5)


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
    ]

    # Add simulation trade generator in sim mode
    if settings.simulation_mode:
        tasks.append(
            asyncio.create_task(simulation_trade_loop(executor), name="sim-trades")
        )

    bot_state.add_log("Bot iniciado · modo simulação" if settings.simulation_mode else "Bot iniciado · LIVE TRADING")
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
    ob_ws = ClobOrderbookWS()
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
