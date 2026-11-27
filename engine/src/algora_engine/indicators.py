"""Streaming indicators for the DSL's ``indicator`` operand. Each is fed one closed candle at a time.

``update(bar)`` returns the value *at the close of that candle* (None while warming up). No look-ahead: a value
depends only on that candle and earlier ones.
"""

from __future__ import annotations

from collections import deque

from .candles import Bar
from .dsl import IndicatorOperand


class Indicator:
    def reset(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def update(self, bar: Bar) -> float | None:  # pragma: no cover - overridden
        raise NotImplementedError


class EMA(Indicator):
    def __init__(self, length: int, field: str) -> None:
        self.length, self.field = length, field
        self.reset()

    def reset(self) -> None:
        self._seed: list[float] = []
        self._value: float | None = None

    def update(self, bar: Bar) -> float | None:
        x = float(getattr(bar, self.field))
        if self._value is None:
            self._seed.append(x)
            if len(self._seed) == self.length:
                self._value = sum(self._seed) / self.length
            return self._value
        alpha = 2 / (self.length + 1)
        self._value += alpha * (x - self._value)
        return self._value


class SMA(Indicator):
    def __init__(self, length: int, field: str) -> None:
        self.length, self.field = length, field
        self.reset()

    def reset(self) -> None:
        self._win: deque[float] = deque(maxlen=self.length)

    def update(self, bar: Bar) -> float | None:
        self._win.append(float(getattr(bar, self.field)))
        return sum(self._win) / self.length if len(self._win) == self.length else None


class Extreme(Indicator):
    """Highest / lowest ``field`` of the ``length`` candles before the current one."""

    def __init__(self, length: int, field: str, highest: bool) -> None:
        self.length, self.field, self.highest = length, field, highest
        self.reset()

    def reset(self) -> None:
        self._win: deque[float] = deque(maxlen=self.length)

    def update(self, bar: Bar) -> float | None:
        value = None
        if len(self._win) == self.length:
            value = max(self._win) if self.highest else min(self._win)
        self._win.append(float(getattr(bar, self.field)))
        return value


class VWAP(Indicator):
    """Session VWAP of the typical price. Unavailable for the rest of the day once a candle lacks volume."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._pv = 0.0
        self._v = 0.0
        self._broken = False

    def update(self, bar: Bar) -> float | None:
        if bar.volume is None:
            self._broken = True
        if self._broken:
            return None
        self._pv += (bar.high + bar.low + bar.close) / 3 * bar.volume
        self._v += bar.volume
        return self._pv / self._v if self._v > 0 else None


def build(op: IndicatorOperand) -> Indicator:
    assert op.length is not None or op.name == "vwap"
    match op.name:
        case "ema":
            return EMA(op.length, op.field)  # type: ignore[arg-type]
        case "sma":
            return SMA(op.length, op.field)  # type: ignore[arg-type]
        case "highest":
            return Extreme(op.length, op.field, True)  # type: ignore[arg-type]
        case "lowest":
            return Extreme(op.length, op.field, False)  # type: ignore[arg-type]
    return VWAP()
