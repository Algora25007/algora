"""Signal generation from DSL entry rules.

:class:`RuleSignals` is fed one closed candle at a time and answers whether that candle produced a long or short
entry signal. It has no notion of orders or fills, so the same object drives backtests, paper and live trading.
"""

from __future__ import annotations

from datetime import date
from enum import IntEnum

from .candles import Bar
from .dsl import Condition, ConstOperand, PriceOperand, SessionOperand, StrategySpec


class Side(IntEnum):
    LONG = 1
    SHORT = -1


class RuleSignals:
    def __init__(self, spec: StrategySpec) -> None:
        self.spec = spec
        self._start = spec.session.start_minute
        self._day: date | None = None
        self._refs: dict[str, float] | None = None
        self._prev: Bar | None = None

    @property
    def levels(self) -> dict[str, float] | None:
        """Session levels for the current day, once the first candle has closed."""
        return dict(self._refs) if self._refs is not None else None

    def start_day(self, day: date) -> None:
        self._day = day
        self._refs = None
        self._prev = None

    def on_bar(self, bar: Bar) -> Side | None:
        """Process a closed candle of the current day; return the entry signal it produced, if any.

        Candles before ``session.start`` are ignored. The first candle at or after the start fixes the session
        levels and never signals itself; rules are evaluated from the next candle on, with the previous candle
        as the "before" side of a cross.
        """
        if bar.day != self._day:
            self.start_day(bar.day)
        if bar.minute < self._start:
            return None
        if self._refs is None:
            self._refs = {
                "first_candle_open": bar.open,
                "first_candle_high": bar.high,
                "first_candle_low": bar.low,
                "first_candle_close": bar.close,
                "day_open": bar.open,
            }
            self._prev = bar
            return None
        prev, self._prev = self._prev, bar
        assert prev is not None
        long_hit = self._all(self.spec.entry.long, bar, prev)
        short_hit = self._all(self.spec.entry.short, bar, prev)
        if long_hit and not short_hit:
            return Side.LONG
        if short_hit and not long_hit:
            return Side.SHORT
        return None

    def _all(self, conds: list[Condition], bar: Bar, prev: Bar) -> bool:
        return bool(conds) and all(self._eval(c, bar, prev) for c in conds)

    def _value(self, operand: PriceOperand | ConstOperand | SessionOperand, bar: Bar) -> float:
        if isinstance(operand, PriceOperand):
            return float(getattr(bar, operand.field))
        if isinstance(operand, ConstOperand):
            return operand.value
        assert self._refs is not None
        return self._refs[operand.ref] * (1 + operand.offset_pct / 100)

    def _eval(self, c: Condition, bar: Bar, prev: Bar) -> bool:
        lv, rv = self._value(c.left, bar), self._value(c.right, bar)
        match c.op:
            case "gt":
                return lv > rv
            case "gte":
                return lv >= rv
            case "lt":
                return lv < rv
            case "lte":
                return lv <= rv
        lp, rp = self._value(c.left, prev), self._value(c.right, prev)
        if c.op == "crosses_above":
            return lv > rv and lp <= rp
        return lv < rv and lp >= rp  # crosses_below
