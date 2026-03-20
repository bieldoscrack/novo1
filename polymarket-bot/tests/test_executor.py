"""Tests for Executor simulation mode."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.executor import Executor
from core.polymarket_client import PolymarketClient


@pytest.fixture
def executor():
    client = MagicMock(spec=PolymarketClient)
    client.create_order = AsyncMock(return_value=None)
    client.cancel_order = AsyncMock(return_value=True)
    return Executor(client)


@pytest.mark.asyncio
async def test_sim_order_creates_position(executor):
    order = await executor.place_maker_order(
        token_id="tok123",
        side="YES",
        price=0.48,
        size=100.0,
        market_name="BTC-5m",
    )
    assert order is not None
    assert order.side == "YES"
    # Position should be created after fill
    pos = executor.positions.get("BTC-5m")
    assert pos is not None
    assert pos.qty_yes == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_sim_pair_position(executor):
    await executor.place_maker_order("tok_yes", "YES", 0.48, 100.0, "BTC-5m")
    await executor.place_maker_order("tok_no", "NO", 0.50, 100.0, "BTC-5m")
    pos = executor.positions.get("BTC-5m")
    assert pos.qty_yes == pytest.approx(100.0)
    assert pos.qty_no == pytest.approx(100.0)
    assert pos.pair_cost_val == pytest.approx(0.98)


@pytest.mark.asyncio
async def test_resolve_market(executor):
    await executor.place_maker_order("tok_yes", "YES", 0.48, 100.0, "BTC-5m")
    await executor.place_maker_order("tok_no", "NO", 0.50, 100.0, "BTC-5m")
    pnl = await executor.resolve_market("BTC-5m", "YES")
    # YES wins: qty_yes*1.0 - cost_yes = 100*1.0 - 48.0 = 52
    assert pnl == pytest.approx(52.0)
    assert "BTC-5m" not in executor.positions
