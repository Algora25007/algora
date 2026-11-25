"""Order execution boundaries.

The trading loop (:mod:`algora_engine.trader`) never prices fills itself; it asks an execution model. Backtests
and paper trading use :class:`SimulatedFills`. Live trading will implement :class:`OrderGateway` inside the
partner broker's infrastructure (see docs/ARCHITECTURE.md): there, every order is a *limit* order and fills arrive
asynchronously from the broker, so the live adapter reconciles broker fills back into the same trader state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from .rules import Side


def _round_to_tick(price: float, tick: float, *, up: bool) -> float:
    steps = price / tick
    # Tolerate float noise so an already-aligned price is not pushed a whole tick.
    n = math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9)
    return round(n * tick, 10)


@dataclass(frozen=True)
class SimulatedFills:
    """Fills for backtests and paper trading.

    * Market-type fills (entries, flips, square-off, stops) are moved against the trader by ``slippage_pct`` percent
      plus ``slippage_points``, then rounded *adversely* to the tick size. With zero slippage the raw price is used.
    * Target exits are limit orders: they fill at the target level, or at the open when the market gaps through it.
    """

    slippage_pct: float = 0.0
    slippage_points: float = 0.0
    tick_size: float = 0.05

    def __post_init__(self) -> None:
        if self.slippage_pct < 0 or self.slippage_points < 0:
            raise ValueError("slippage must be non-negative")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")

    def market(self, price: float, order_side: Side) -> float:
        """Fill price for a market-type order; ``order_side`` LONG = buy, SHORT = sell."""
        if self.slippage_pct == 0 and self.slippage_points == 0:
            return price
        if order_side is Side.LONG:
            return _round_to_tick(price * (1 + self.slippage_pct / 100) + self.slippage_points, self.tick_size, up=True)
        p = price * (1 - self.slippage_pct / 100) - self.slippage_points
        return max(_round_to_tick(p, self.tick_size, up=False), self.tick_size)

    def limit(self, level: float) -> float:
        return level


class OrderGateway(Protocol):  # pragma: no cover - interface only; implemented by the broker-hosted executor
    """What a live executor must provide. Orders are always limit orders (exchange retail-algo requirement)."""

    def place_limit(self, *, symbol: str, side: Side, qty: int, price: float, tag: str) -> str: ...

    def cancel(self, order_id: str) -> None: ...
