"""
Telegram alerts — optional. Falls back gracefully if not configured.
"""
from __future__ import annotations
import asyncio
from typing import Optional

from loguru import logger

from config import settings


class TelegramAlerter:
    def __init__(self) -> None:
        self._bot: Optional[object] = None
        self._chat_id: str = settings.telegram_chat_id
        self._enabled: bool = bool(
            settings.telegram_bot_token and settings.telegram_chat_id
        )

    async def init(self) -> None:
        if not self._enabled:
            logger.info("Telegram não configurado — alertas desativados")
            return
        try:
            from telegram import Bot
            self._bot = Bot(token=settings.telegram_bot_token)
            await self.send("🤖 Polymarket Bot iniciado!")
        except ImportError:
            logger.warning("python-telegram-bot não instalado")
            self._enabled = False
        except Exception as e:
            logger.warning(f"Telegram init erro: {e}")
            self._enabled = False

    async def send(self, text: str) -> None:
        if not self._enabled or not self._bot:
            return
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.debug(f"Telegram send falhou: {e}")

    async def trade_alert(
        self, market: str, side: str, price: float, qty: float, sim: bool
    ) -> None:
        mode = "SIM" if sim else "LIVE"
        await self.send(
            f"[{mode}] 📊 <b>{side}</b> {market}\n"
            f"Preço: <code>${price:.3f}</code> · Qty: <code>{qty:.0f}</code>"
        )

    async def pnl_daily(self, pnl: float, wins: int, losses: int) -> None:
        emoji = "📈" if pnl >= 0 else "📉"
        await self.send(
            f"{emoji} <b>P&L Diário:</b> <code>${pnl:+.2f}</code>\n"
            f"Wins: {wins} · Losses: {losses} · "
            f"Win Rate: {wins/(wins+losses)*100:.0f}%"
            if (wins + losses) > 0
            else f"{emoji} <b>P&L Diário:</b> <code>${pnl:+.2f}</code>"
        )

    async def error_alert(self, msg: str) -> None:
        await self.send(f"⚠️ <b>ERRO:</b> {msg}")

    async def stop_loss_alert(self, loss_pct: float, pause_hours: int) -> None:
        await self.send(
            f"🛑 <b>STOP-LOSS ATIVADO</b>\n"
            f"Perda: <code>{loss_pct*100:.1f}%</code> em 24h\n"
            f"Bot pausado por <code>{pause_hours}h</code>"
        )
