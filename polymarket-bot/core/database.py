"""
SQLite trade log via SQLAlchemy async.
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import Column, Float, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


DB_PATH = Path(__file__).parent.parent / "data" / "trades.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class TradeRecord(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(Float, default=time.time)
    market = Column(String(200))
    side = Column(String(10))
    price = Column(Float)
    qty = Column(Float)
    pnl = Column(Float, nullable=True)
    simulation = Column(Integer, default=1)
    notes = Column(Text, nullable=True)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info(f"Database inicializado: {DB_PATH}")


async def log_trade(
    market: str,
    side: str,
    price: float,
    qty: float,
    pnl: Optional[float] = None,
    simulation: bool = True,
    notes: str = "",
) -> None:
    try:
        async with AsyncSessionLocal() as session:
            record = TradeRecord(
                timestamp=time.time(),
                market=market,
                side=side,
                price=price,
                qty=qty,
                pnl=pnl,
                simulation=int(simulation),
                notes=notes,
            )
            session.add(record)
            await session.commit()
    except Exception as e:
        logger.warning(f"DB log_trade erro: {e}")


async def get_recent_trades(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        from sqlalchemy import select, desc
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TradeRecord).order_by(desc(TradeRecord.timestamp)).limit(limit)
            )
            rows = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "timestamp": r.timestamp,
                    "market": r.market,
                    "side": r.side,
                    "price": r.price,
                    "qty": r.qty,
                    "pnl": r.pnl,
                    "simulation": bool(r.simulation),
                }
                for r in rows
            ]
    except Exception as e:
        logger.warning(f"DB get_recent_trades erro: {e}")
        return []
