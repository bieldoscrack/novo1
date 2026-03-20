"""Tests for BinanceFeed message handling."""
import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock

from core.binance_feed import BinanceFeed


@pytest.fixture
def feed():
    return BinanceFeed()


@pytest.mark.asyncio
async def test_handle_trade_message(feed):
    msg = json.dumps({
        "stream": "btcusdt@trade",
        "data": {
            "e": "trade",
            "s": "BTCUSDT",
            "p": "87654.32",
            "q": "0.001",
            "T": 1711000000000
        }
    })
    await feed._handle(msg)
    assert feed.prices["BTC"] == pytest.approx(87654.32)


@pytest.mark.asyncio
async def test_handle_eth_trade(feed):
    msg = json.dumps({
        "data": {"e": "trade", "s": "ETHUSDT", "p": "3421.10", "q": "0.1", "T": 0}
    })
    await feed._handle(msg)
    assert feed.prices["ETH"] == pytest.approx(3421.10)


@pytest.mark.asyncio
async def test_callback_called(feed):
    received = {}
    def cb(prices):
        received.update(prices)

    feed.on_price(cb)
    msg = json.dumps({
        "data": {"e": "trade", "s": "SOLUSDT", "p": "178.50", "q": "5", "T": 0}
    })
    await feed._handle(msg)
    assert received.get("SOL") == pytest.approx(178.50)


@pytest.mark.asyncio
async def test_invalid_json_ignored(feed):
    await feed._handle("not json {{")
    assert feed.prices["BTC"] == 0.0
