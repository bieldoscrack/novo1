"""
Abstract base strategy class.
"""
from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from typing import Any


class BaseStrategy(ABC):
    def __init__(self) -> None:
        self._running: bool = False

    @abstractmethod
    async def run(self) -> None:
        ...

    def stop(self) -> None:
        self._running = False
