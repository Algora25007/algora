"""Backtest runner: wires candles, the shared :class:`~algora_engine.trader.Trader`, charges and metrics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import __version__
from . import calendar as cal
from .candles import Bar, detect_timeframe
from .charges import DISCOUNT_BROKER_PLAN, BrokeragePlan, ChargesBreakdown, compute_charges, rates_on
from .dsl import StrategySpec, hhmm_to_minutes
from .execution import SimulatedFills
from .instruments import Segment
from .lots import lot_size
from .metrics import equity_curve, per_day, summarize
from .rules import Side
from .trader import ClosedTrade, RiskLimits, Trader


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    daily_loss_limit: Annotated[float | None, Field(ge=0, le=1e9)] = None
    max_trades_per_day: Annotated[int | None, Field(ge=1, le=500)] = None
    stop_new_entries_after: str | None = None

    @field_validator("stop_new_entries_after")
    @classmethod
    def _hhmm(cls, v: str | None) -> str | None:
        if v is not None:
            hhmm_to_minutes(v)
        return v


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    slippage_pct: Annotated[float, Field(ge=0, le=2)] = 0.0
    slippage_points: Annotated[float, Field(ge=0, le=10_000)] = 0.0
    apply_charges: bool = True
    brokerage: BrokeragePlan = DISCOUNT_BROKER_PLAN
    risk: RiskConfig | None = None
    #: Cap the square-off at the exchange close for the day (e.g. 15:30 for expiring index contracts from
    #: 7 Dec 2026). Disable only to reproduce old results.
    use_exchange_calendar: bool = True


class BacktestError(ValueError):
    pass


@dataclass
class BacktestResult:
    trades: list[ClosedTrade]
    summary: dict
    equity: list[dict]
    days: list[dict]
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "summary": self.summary,
            "equity_curve": self.equity,
            "per_day": self.days,
            "trades": [t.as_dict() for t in self.trades],
            "warnings": self.warnings,
            "meta": self.meta,
        }


def resample(bars: Sequence[Bar], minutes: int, anchor_minute: int) -> list[Bar]:
    """Aggregate intraday candles into ``minutes`` buckets aligned to ``anchor_minute`` (the session start).

    Volume is summed when every input candle carries one. Input must be sorted and of a smaller timeframe.
    """
    out: list[Bar] = []
    cur: list[Bar] = []
    key: tuple[date, int] | None = None

    def flush() -> None:
        if not cur:
            return
        assert key is not None
        day, bucket = key
        start = datetime(day.year, day.month, day.day) + timedelta(minutes=anchor_minute + bucket * minutes)
        vols = [b.volume for b in cur]
        out.append(
            Bar(
                ts=start,
                open=cur[0].open,
                high=max(b.high for b in cur),
                low=min(b.low for b in cur),
                close=cur[-1].close,
                volume=sum(v for v in vols if v is not None) if all(v is not None for v in vols) else None,
            )
        )

    for b in bars:
        k = (b.day, (b.minute - anchor_minute) // minutes)
        if k != key:
            flush()
            cur, key = [], k
        cur.append(b)
    flush()
    return out


def _charges_fn(spec: StrategySpec, config: BacktestConfig):
    inst = spec.instrument

    def fn(t: ClosedTrade) -> ChargesBreakdown | None:
        entry_value = Decimal(str(t.entry_price)) * t.qty
        exit_value = Decimal(str(t.exit_price)) * t.qty
        buy, sell = (entry_value, exit_value) if t.side is Side.LONG else (exit_value, entry_value)
        return compute_charges(
            segment=inst.segment,
            exchange=inst.exchange,
            trade_date=t.day,
            buy_value=buy,
            sell_value=sell,
            plan=config.brokerage,
            agri=inst.agri,
        )

    return fn


def run_backtest(spec: StrategySpec, bars: Sequence[Bar], config: BacktestConfig | None = None) -> BacktestResult:
    config = config or BacktestConfig()
    if not bars:
        raise BacktestError("no candles to test")
    bars = sorted(bars, key=lambda b: b.ts)
    warnings: list[str] = []
    inst = spec.instrument

    src_tf = detect_timeframe(bars)
    if src_tf == 0:
        raise BacktestError("intraday candles are required (the file looks like daily data)")
    want = spec.timeframe_minutes
    if src_tf > want:
        raise BacktestError(f"candles are {src_tf}m but the strategy runs on {spec.timeframe}; upload smaller candles")
    if src_tf < want:
        if want % src_tf:
            raise BacktestError(f"{src_tf}m candles cannot be combined into {spec.timeframe} candles")
        bars = resample(bars, want, spec.session.start_minute)
        warnings.append(f"Combined {src_tf}m candles into {spec.timeframe} candles from {spec.session.start}.")

    days = sorted({b.day for b in bars})
    commodity = inst.segment in (Segment.COMMODITY_FUTURES, Segment.COMMODITY_OPTIONS)
    closed_days = [
        d
        for d in days
        if cal.is_known_year(d.year) and (cal.sessions(d).mcx is None if commodity else not cal.is_nse_trading_day(d))
    ]
    if closed_days:
        warnings.append(
            f"{len(closed_days)} session(s) fall on exchange holidays or weekends, e.g. {closed_days[0].isoformat()}."
        )
    unknown_years = sorted({d.year for d in days if not cal.is_known_year(d.year)})
    if unknown_years and config.use_exchange_calendar:
        warnings.append(
            "No exchange holiday list on file for "
            + ", ".join(map(str, unknown_years))
            + "; those days use standard session times."
        )

    # Lot sizes (validated up front so a missing lot fails before any work).
    lots_used: dict[int, str] = {}
    lot_cache: dict[date, int] = {}
    for d in days:
        info = lot_size(inst.symbol, d, inst.segment, inst.lot_size)
        lot_cache[d] = info.size
        if info.size not in lots_used:
            lots_used[info.size] = info.source
            if not info.verified:
                warnings.append(f"Lot size {info.size} for {inst.symbol} is not verified ({info.source}).")

    def square_off(d: date) -> int:
        sq = spec.session.square_off_minute
        if not config.use_exchange_calendar:
            return sq
        close = cal.market_close(d, inst.segment, inst.symbol)
        return min(sq, close) if close is not None else sq

    capped = [d for d in days if square_off(d) < spec.session.square_off_minute]
    if capped:
        warnings.append(
            f"Square-off moved earlier on {len(capped)} day(s) to the exchange close "
            f"(e.g. {capped[0].isoformat()}, expiring index contracts stop at 15:30 from 7 Dec 2026)."
        )

    charges_fn = _charges_fn(spec, config) if config.apply_charges else None
    charges_sources: list[str] = []
    if config.apply_charges:
        seen: set[tuple] = set()
        for d in days:
            r = rates_on(inst.segment, inst.exchange, d, agri=inst.agri)
            sig = (r.stt_sell, r.txn, r.extrapolated)
            if sig in seen:
                continue
            seen.add(sig)
            charges_sources.extend(s for s in r.sources if s not in charges_sources)
            if r.extrapolated:
                warnings.append("Charges for dates before 1 Oct 2024 use the 1 Oct 2024 rate table.")
            if not r.all_verified:
                warnings.append(f"Some {inst.segment.value} rates on {inst.exchange.value} are not verified.")

    risk = None
    if config.risk:
        risk = RiskLimits(
            daily_loss_limit=config.risk.daily_loss_limit,
            max_trades_per_day=config.risk.max_trades_per_day,
            stop_new_entries_after=config.risk.stop_new_entries_after,
        )
    trader = Trader(
        spec,
        SimulatedFills(config.slippage_pct, config.slippage_points, inst.tick_size),
        qty_for_day=lambda d: lot_cache[d],
        square_off_for_day=square_off,
        charges_fn=charges_fn,
        risk=risk,
    )
    trades: list[ClosedTrade] = []
    for b in bars:
        trades.extend(trader.on_bar(b))
    trades.extend(trader.finish())

    return BacktestResult(
        trades=trades,
        summary=summarize(trades),
        equity=equity_curve(trades),
        days=per_day(trades),
        warnings=warnings,
        meta={
            "engine_version": __version__,
            "schema_version": spec.schema_version,
            "candles": len(bars),
            "sessions": len(days),
            "from": days[0].isoformat(),
            "to": days[-1].isoformat(),
            "source_timeframe_minutes": src_tf,
            "timeframe": spec.timeframe,
            "lot_sizes": {str(k): v for k, v in lots_used.items()},
            "charges_sources": charges_sources,
            "fill_model": {
                "entries": "next candle open + slippage",
                "slippage_pct": config.slippage_pct,
                "slippage_points": config.slippage_points,
                "same_candle_stop_and_target": "stop first",
            },
        },
    )
