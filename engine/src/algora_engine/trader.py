"""The event-driven trading loop shared by backtest, paper and (later) live execution.

Feed closed candles in time order to :meth:`Trader.on_bar`; it returns the trades each candle closed.
Call :meth:`Trader.finish` after the last candle.

Order of events inside one candle (all fills are decided without looking ahead):

0. Every candle of an open day (including ones before the session start or after square-off) updates the
   indicators and session state in :class:`~algora_engine.rules.RuleSignals`. A day the exchange is closed
   (``square_off_for_day`` returns None) is skipped entirely.
1. If the candle starts at or after the day's square-off time: cancel pending orders, close any position at the
   candle's open (market, with slippage), and ignore the rest of the day.
2. Orders queued by the previous candle's signal fill at this candle's open, with slippage: first a flip exit,
   then the entry (subject to max entries per day and risk limits).
3. Protective exits for the open position:
   a. Gap-through at the open: if the open is already beyond the stop, exit at the open (with slippage); if it is
      already beyond the target, the resting limit fills at the open. On the entry candle the check is against the
      entry fill instead: a stop or target level that is already on the wrong side of the fill (possible with level
      exits after a gap) closes the trade at that open, stop first.
   b. Intrabar: if the stop (fixed or trailing, whichever is tighter) was touched, exit at the stop. Only otherwise
      check the target. When both were touched in one candle, the stop is assumed to have hit first (conservative).
4. The trailing-stop anchor (best price since entry) is updated with this candle's high/low *after* step 3.
5. The candle's close is evaluated by the entry rules and exit conditions; a signal queues orders for the next
   candle's open: a flip (opposite entry signal with ``exits.flip``) or a rule exit closes the position, and an
   opposite signal then opens the new one (subject to the entry limits).

At the end of a day without a square-off candle, an open position is closed at the last candle's close.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from .candles import Bar
from .charges import ChargesBreakdown
from .dsl import StrategySpec, hhmm_to_minutes
from .execution import SimulatedFills
from .rules import RuleSignals, Side


@dataclass(frozen=True)
class RiskLimits:
    daily_loss_limit: float | None = None  # rupees of realised net loss after which the day stops
    max_trades_per_day: int | None = None
    stop_new_entries_after: str | None = None  # HH:MM IST

    @property
    def cutoff_minute(self) -> int | None:
        return hhmm_to_minutes(self.stop_new_entries_after) if self.stop_new_entries_after else None


@dataclass
class Position:
    side: Side
    qty: int
    entry_ts: datetime
    entry_price: float
    stop: float | None
    target: float | None
    trail_pct: float | None
    best: float  # highest high (long) / lowest low (short) since entry, for the trailing stop

    def trail_level(self) -> float | None:
        if self.trail_pct is None:
            return None
        return self.best * (1 - self.side * self.trail_pct / 100)

    def stop_level(self) -> tuple[float | None, bool]:
        """Effective stop and whether the trailing stop is the binding one."""
        trail = self.trail_level()
        if trail is None:
            return self.stop, False
        if self.stop is None:
            return trail, True
        if self.side is Side.LONG:
            return (trail, True) if trail > self.stop else (self.stop, False)
        return (trail, True) if trail < self.stop else (self.stop, False)


@dataclass(frozen=True)
class ClosedTrade:
    day: date
    side: Side
    qty: int
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime
    exit_price: float
    exit_reason: str
    points: float
    gross_pnl: float
    charges: ChargesBreakdown | None
    net_pnl: float

    def as_dict(self) -> dict:
        return {
            "day": self.day.isoformat(),
            "side": "Long" if self.side is Side.LONG else "Short",
            "qty": self.qty,
            "entry_time": self.entry_ts.isoformat(timespec="minutes"),
            "entry_price": round(self.entry_price, 4),
            "exit_time": self.exit_ts.isoformat(timespec="minutes"),
            "exit_price": round(self.exit_price, 4),
            "reason": self.exit_reason,
            "points": round(self.points, 4),
            "gross_pnl": round(self.gross_pnl, 2),
            "charges": self.charges.as_dict() if self.charges else None,
            "net_pnl": round(self.net_pnl, 2),
        }


ChargesFn = Callable[[ClosedTrade], ChargesBreakdown | None]
QtyFn = Callable[[date], int]
SquareOffFn = Callable[[date], int | None]  # None: the exchange is closed that day


@dataclass
class _Day:
    day: date | None = None
    started: bool = False
    skip: bool = False
    squared: bool = False
    square_off: int = 0
    entries: int = 0
    net: float = 0.0
    halted: bool = False
    last_bar: Bar | None = None
    pending: list[tuple[str, Side | None, str]] = field(default_factory=list)


class Trader:
    def __init__(
        self,
        spec: StrategySpec,
        fills: SimulatedFills,
        *,
        qty_for_day: QtyFn,
        square_off_for_day: SquareOffFn | None = None,
        charges_fn: ChargesFn | None = None,
        risk: RiskLimits | None = None,
    ) -> None:
        self.spec = spec
        self.fills = fills
        self.signals = RuleSignals(spec)
        self._qty_for_day = qty_for_day
        self._square_off_for_day = square_off_for_day or (lambda _d: spec.session.square_off_minute)
        self._charges_fn = charges_fn
        self.risk = risk or RiskLimits()
        self.position: Position | None = None
        self._d = _Day()
        self._start = spec.session.start_minute

    # ----- public API -------------------------------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> list[ClosedTrade]:
        out: list[ClosedTrade] = []
        d = self._d
        if d.last_bar is not None and bar.ts <= d.last_bar.ts:
            raise ValueError(f"candles must be in increasing time order ({bar.ts} after {d.last_bar.ts})")
        if bar.day != d.day:
            out += self._end_day()
            self._start_day(bar.day)
            d = self._d
        if d.skip:
            return out
        if bar.minute < self._start or bar.minute >= d.square_off:
            self.signals.on_bar(bar, evaluate=False)
        if bar.minute < self._start:
            return out
        if not d.started:
            d.started = True
            if bar.minute >= d.square_off:
                d.skip = True
                return out
        if bar.minute >= d.square_off:
            if not d.squared:
                d.pending = []
                if self.position is not None:
                    px = self.fills.market(bar.open, Side(-self.position.side))
                    out.append(self._close(bar.ts, px, "Square-off"))
                d.squared = True
            return out

        entered_now = self._fill_pending(bar, out)
        if self.position is not None:
            self._protective(bar, entered_now, out)
        if self.position is not None:
            p = self.position
            p.best = max(p.best, bar.high) if p.side is Side.LONG else min(p.best, bar.low)
        self._queue(self.signals.on_bar(bar), bar)
        d.last_bar = bar
        return out

    def finish(self) -> list[ClosedTrade]:
        return self._end_day()

    # ----- internals --------------------------------------------------------------------------------------------

    def _start_day(self, day: date) -> None:
        sq = self._square_off_for_day(day)
        self._d = _Day(day=day, square_off=sq if sq is not None else 0, skip=sq is None)
        if sq is not None:
            self.signals.start_day(day)

    def _end_day(self) -> list[ClosedTrade]:
        d = self._d
        d.pending = []
        if self.position is not None and d.last_bar is not None:
            bar = d.last_bar
            px = self.fills.market(bar.close, Side(-self.position.side))
            return [self._close(bar.ts, px, "Session end")]
        return []

    def _entries_allowed(self, minute: int) -> bool:
        d, r = self._d, self.risk
        if d.halted or d.entries >= self.spec.risk.max_entries_per_day:
            return False
        if r.max_trades_per_day is not None and d.entries >= r.max_trades_per_day:
            return False
        cutoff = r.cutoff_minute
        return not (cutoff is not None and minute >= cutoff)

    def _queue(self, sig: Side | None, bar: Bar) -> None:
        pos = self.position
        orders: list[tuple[str, Side | None, str]] = []
        if pos is None:
            if sig is not None and self._entries_allowed(bar.minute):
                orders.append(("entry", sig, ""))
        else:
            reverse = sig is not None and sig is not pos.side
            if reverse and self.spec.exits.flip:
                orders.append(("exit", None, "Flip"))
            elif self.signals.exit_signal(pos.side):
                orders.append(("exit", None, "Exit rule"))
            if orders and reverse and self._entries_allowed(bar.minute):
                orders.append(("entry", sig, ""))
        self._d.pending = orders

    def _fill_pending(self, bar: Bar, out: list[ClosedTrade]) -> bool:
        d = self._d
        pending, d.pending = d.pending, []
        entered = False
        for kind, side, reason in pending:
            if kind == "exit" and self.position is not None:
                px = self.fills.market(bar.open, Side(-self.position.side))
                out.append(self._close(bar.ts, px, reason))
            elif kind == "entry" and side is not None and self.position is None and self._entries_allowed(bar.minute):
                self._open(bar, side)
                entered = True
        return entered

    def _side_level(self, levels, side: Side) -> float | None:
        if levels is None:
            return None
        op = levels.long if side is Side.LONG else levels.short
        return self.signals.level(op) if op is not None else None

    def _open(self, bar: Bar, side: Side) -> None:
        ex = self.spec.exits
        price = self.fills.market(bar.open, side)
        stop = price * (1 - side * ex.stop_pct / 100) if ex.stop_pct is not None else None
        if ex.stop_level is not None:
            stop = self._side_level(ex.stop_level, side)
        target = price * (1 + side * ex.target_pct / 100) if ex.target_pct is not None else None
        if ex.target_level is not None:
            target = self._side_level(ex.target_level, side)
        elif ex.target_r_multiple is not None and stop is not None and (price - stop) * side > 0:
            target = price + side * ex.target_r_multiple * abs(price - stop)
        elif ex.target_range_multiple is not None:
            width = self.signals.range_width()
            target = price + side * ex.target_range_multiple * width if width else None
        qty = self.spec.risk.quantity_lots * self._qty_for_day(bar.day)
        self.position = Position(side, qty, bar.ts, price, stop, target, ex.trailing_stop_pct, price)
        self._d.entries += 1

    def _protective(self, bar: Bar, entered_now: bool, out: list[ClosedTrade]) -> None:
        p = self.position
        assert p is not None
        stop, trailing = p.stop_level()
        stop_reason = "Trailing stop" if trailing else "Stop"
        exit_side = Side(-p.side)
        long = p.side is Side.LONG
        o = bar.open
        ref = p.entry_price if entered_now else o
        if stop is not None and (ref <= stop if long else ref >= stop):
            out.append(self._close(bar.ts, self.fills.market(o, exit_side), stop_reason))
            return
        if p.target is not None and (ref >= p.target if long else ref <= p.target):
            out.append(self._close(bar.ts, self.fills.limit(o), "Target"))
            return
        if stop is not None and (bar.low <= stop if long else bar.high >= stop):
            out.append(self._close(bar.ts, self.fills.market(stop, exit_side), stop_reason))
            return
        if p.target is not None and (bar.high >= p.target if long else bar.low <= p.target):
            out.append(self._close(bar.ts, self.fills.limit(p.target), "Target"))

    def _close(self, ts: datetime, price: float, reason: str) -> ClosedTrade:
        p = self.position
        assert p is not None
        self.position = None
        points = (price - p.entry_price) * p.side
        gross = points * p.qty
        draft = ClosedTrade(
            day=ts.date(),
            side=p.side,
            qty=p.qty,
            entry_ts=p.entry_ts,
            entry_price=p.entry_price,
            exit_ts=ts,
            exit_price=price,
            exit_reason=reason,
            points=points,
            gross_pnl=gross,
            charges=None,
            net_pnl=gross,
        )
        charges = self._charges_fn(draft) if self._charges_fn else None
        net = gross - float(charges.total) if charges else gross
        trade = replace(draft, charges=charges, net_pnl=net)
        d = self._d
        d.net += net
        limit = self.risk.daily_loss_limit
        if limit is not None and d.net <= -limit:
            d.halted = True
            d.pending = [o for o in d.pending if o[0] != "entry"]
        return trade
