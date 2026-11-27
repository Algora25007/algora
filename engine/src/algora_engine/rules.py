"""Signal generation from DSL entry rules.

:class:`RuleSignals` is fed every closed candle of an open trading day in time order and answers whether that
candle produced a long or short entry signal. It has no notion of orders or fills, so the same object drives
backtests, paper and live trading.

What it tracks:

* Indicators (EMA, SMA, highest/lowest, VWAP): updated on every candle it is fed, before rules are evaluated.
* The day's first candle (the first one starting at or after ``session.start``) and ``day_open``.
* The opening range: the high/low of the candles starting in ``[first candle, first candle + opening_range)``.
  It is formed when the last candle in the window closes (or, if that candle is missing, when the first candle
  after the window arrives). Without ``session.opening_range_minutes`` the range is the first candle.
* Previous-session open/high/low/close: taken from the latest earlier day in the data, and only when the exchange
  calendar has no trading day between that day and today.

Rules are evaluated from the candle after the opening range (``signals_from="after_opening_range"``, the v1
behaviour) or from the first candle (``"session_start"``). ``crosses_*`` compare with the previous candle of the
same day, so a cross never spans two days. A condition whose operand is unavailable is false.
"""

from __future__ import annotations

from datetime import date
from enum import IntEnum
from typing import Any

from . import calendar as cal
from . import indicators as ind
from .candles import Bar
from .dsl import (
    Condition,
    ConstOperand,
    IndicatorOperand,
    PrevSessionOperand,
    PriceOperand,
    SessionOperand,
    StrategySpec,
    TimeCondition,
    hhmm_to_minutes,
)


class Side(IntEnum):
    LONG = 1
    SHORT = -1


_FIRST = ("first_candle_open", "first_candle_high", "first_candle_low", "first_candle_close", "day_open")


class RuleSignals:
    def __init__(self, spec: StrategySpec) -> None:
        self.spec = spec
        self._start = spec.session.start_minute
        self._tf = spec.timeframe_minutes
        self._or_len = spec.opening_range_length
        self._after_range = spec.session.signals_from == "after_opening_range"
        self._segment = spec.instrument.segment
        self._indicators: dict[tuple, ind.Indicator] = {}
        self._session_scoped: list[ind.Indicator] = []
        for op in spec.operands():
            if isinstance(op, IndicatorOperand) and op.key not in self._indicators:
                self._indicators[op.key] = ind.build(op)
                if op.scope == "session":
                    self._session_scoped.append(self._indicators[op.key])
        self._uses_prev = spec.uses_prev_session()
        #: Whether the last evaluated candle met the exit conditions for a long / short position.
        self.exit_long = False
        self.exit_short = False
        #: Per day, when the strategy reads previous-session values: "ok", "unverified", "missing" or "none".
        self.prev_session_status: dict[date, str] = {}
        self._day: date | None = None
        self._last_session: tuple[date, dict[str, float]] | None = None
        self._today: dict[str, float] | None = None
        self._prev_session: dict[str, float] | None = None
        self._reset_day()

    # ----- day state --------------------------------------------------------------------------------------------

    def _reset_day(self) -> None:
        self._refs: dict[str, float] | None = None
        self._range: dict[str, float] | None = None
        self._range_done = False
        self._range_end = 0
        self._prev: tuple[Bar, dict[tuple, float | None]] | None = None

    @property
    def levels(self) -> dict[str, float] | None:
        """Session levels for the current day, once the first candle has closed."""
        if self._refs is None:
            return None
        out = dict(self._refs)
        if self._range_done and self._range is not None:
            out.update(self._range)
        return out

    @property
    def prev_session(self) -> dict[str, float] | None:
        return dict(self._prev_session) if self._prev_session is not None else None

    def start_day(self, day: date) -> None:
        if day == self._day:
            return
        if self._day is not None and self._today is not None:
            self._last_session = (self._day, self._today)
        self._day = day
        self._today = None
        self._reset_day()
        for i in self._session_scoped:
            i.reset()
        self._prev_session = None
        status = "none"
        if self._last_session is not None:
            status = cal.previous_session_check(self._last_session[0], day, self._segment)
            if status != "missing":
                self._prev_session = self._last_session[1]
        if self._uses_prev:
            self.prev_session_status[day] = status

    # ----- per candle -------------------------------------------------------------------------------------------

    def exit_signal(self, side: Side) -> bool:
        return self.exit_long if side is Side.LONG else self.exit_short

    def on_bar(self, bar: Bar, *, evaluate: bool = True) -> Side | None:
        """Process a closed candle of the current day; return the entry signal it produced, if any.

        Exit conditions are evaluated on the same candle; read them with :meth:`exit_signal`.

        ``evaluate=False`` updates indicators and session state without evaluating rules (used for candles outside
        the trading window, e.g. after square-off, so previous-session values cover the whole day).
        """
        if bar.day != self._day:
            self.start_day(bar.day)
        vals = {k: i.update(bar) for k, i in self._indicators.items()}
        t = self._today
        if t is None:
            self._today = {"open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close}
        else:
            t["high"], t["low"], t["close"] = max(t["high"], bar.high), min(t["low"], bar.low), bar.close
        if bar.minute < self._start:
            return None
        if self._refs is None:
            self._refs = dict(zip(_FIRST, (bar.open, bar.high, bar.low, bar.close, bar.open), strict=True))
            self._range_end = bar.minute + self._or_len
        formed_now = False
        if not self._range_done:
            if bar.minute >= self._range_end:
                self._finish_range()
            else:
                r = self._range
                if r is None:
                    self._range = {"range_open": bar.open, "range_high": bar.high, "range_low": bar.low}
                else:
                    r["range_high"], r["range_low"] = max(r["range_high"], bar.high), min(r["range_low"], bar.low)
                assert self._range is not None
                self._range["range_close"] = bar.close
                if bar.minute + self._tf >= self._range_end:
                    self._finish_range()
                    formed_now = True
        prev, self._prev = self._prev, (bar, vals)
        self.exit_long = self.exit_short = False
        if not evaluate or (self._after_range and (formed_now or not self._range_done)):
            return None
        xc = self.spec.exits.conditions
        if xc is not None:
            self.exit_long = self._all(xc.long, bar, vals, prev)
            self.exit_short = self._all(xc.short, bar, vals, prev)
        long_hit = self._all(self.spec.entry.long, bar, vals, prev)
        short_hit = self._all(self.spec.entry.short, bar, vals, prev)
        if long_hit and not short_hit:
            return Side.LONG
        if short_hit and not long_hit:
            return Side.SHORT
        return None

    def _finish_range(self) -> None:
        r = self._range
        assert r is not None  # the first candle always starts the range
        r["range_mid"] = (r["range_high"] + r["range_low"]) / 2
        self._range_done = True

    # ----- values -----------------------------------------------------------------------------------------------

    def range_width(self) -> float | None:
        if not self._range_done or self._range is None:
            return None
        return self._range["range_high"] - self._range["range_low"]

    def level(self, op: SessionOperand | PrevSessionOperand) -> float | None:
        """Current value of a day-fixed operand (used for stop / target levels)."""
        return self._fixed(op)

    def _fixed(self, op: SessionOperand | PrevSessionOperand) -> float | None:
        if isinstance(op, PrevSessionOperand):
            base = self._prev_session.get(op.field) if self._prev_session else None
        else:
            ref = op.ref
            if ref in _FIRST:
                base = self._refs.get(ref) if self._refs else None
            elif ref == "gap_pct":
                pc = self._prev_session.get("close") if self._prev_session else None
                if pc is None or self._refs is None:
                    return None
                return (self._refs["day_open"] - pc) / pc * 100
            elif not self._range_done or self._range is None:
                return None
            elif ref == "range_width_pct":
                r = self._range
                return (r["range_high"] - r["range_low"]) / r["range_close"] * 100
            else:
                base = self._range[ref]
        if base is None:
            return None
        return base * (1 + op.offset_pct / 100) + op.offset_points

    def _value(self, op: Any, bar: Bar, vals: dict[tuple, float | None]) -> float | None:
        if isinstance(op, PriceOperand):
            return float(getattr(bar, op.field))
        if isinstance(op, ConstOperand):
            return op.value
        if isinstance(op, IndicatorOperand):
            return vals[op.key]
        return self._fixed(op)

    def _all(self, conds: list, bar: Bar, vals: dict, prev: tuple[Bar, dict] | None) -> bool:
        return bool(conds) and all(self._eval(c, bar, vals, prev) for c in conds)

    def _eval(self, c: Condition | TimeCondition, bar: Bar, vals: dict, prev: tuple[Bar, dict] | None) -> bool:
        if isinstance(c, TimeCondition):
            close_minute = bar.minute + self._tf
            return hhmm_to_minutes(c.start) <= close_minute <= hhmm_to_minutes(c.end)
        lv, rv = self._value(c.left, bar, vals), self._value(c.right, bar, vals)
        if lv is None or rv is None:
            return False
        match c.op:
            case "gt":
                return lv > rv
            case "gte":
                return lv >= rv
            case "lt":
                return lv < rv
            case "lte":
                return lv <= rv
        if prev is None:
            return False
        lp, rp = self._value(c.left, *prev), self._value(c.right, *prev)
        if lp is None or rp is None:
            return False
        if c.op == "crosses_above":
            return lv > rv and lp <= rp
        return lv < rv and lp >= rp  # crosses_below
