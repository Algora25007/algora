"""Strategy templates expressed on the DSL. The engine executes only the DSL; templates just build it."""

from __future__ import annotations

from .dsl import (
    Condition,
    EntryRules,
    Exits,
    Instrument,
    PriceOperand,
    Risk,
    Session,
    SessionOperand,
    StrategySpec,
    Timeframe,
)


def first_candle_cross(
    *,
    name: str,
    instrument: Instrument,
    timeframe: Timeframe = "15m",
    buffer_pct: float = 0.0,
    start: str = "09:15",
    square_off: str = "15:15",
    target_pct: float | None = None,
    stop_pct: float | None = None,
    trailing_stop_pct: float | None = None,
    flip: bool = True,
    max_entries_per_day: int = 1,
    quantity_lots: int = 1,
    direction: str = "both",
) -> StrategySpec:
    """First Candle Cross.

    The first candle of the day (starting at ``start``) sets two levels: its high plus ``buffer_pct`` and its low
    minus ``buffer_pct``. Go long when a candle closes above the upper level after the previous candle closed at or
    below it; go short on the mirror-image cross of the lower level. Orders fill at the next candle's open.
    """
    if not 0 <= buffer_pct <= 5:
        raise ValueError("buffer_pct must be 0 to 5")
    if direction not in ("both", "long", "short"):
        raise ValueError("direction must be both, long or short")
    close = PriceOperand(field="close")
    up = SessionOperand(ref="first_candle_high", offset_pct=buffer_pct)
    dn = SessionOperand(ref="first_candle_low", offset_pct=-buffer_pct if buffer_pct else 0.0)  # no -0.0
    long_rules = [Condition(op="crosses_above", left=close, right=up)] if direction in ("both", "long") else []
    short_rules = [Condition(op="crosses_below", left=close, right=dn)] if direction in ("both", "short") else []
    return StrategySpec(
        name=name,
        template="first_candle_cross",
        template_params={"buffer_pct": buffer_pct, "direction": direction},
        instrument=instrument,
        timeframe=timeframe,
        session=Session(start=start, square_off=square_off),
        entry=EntryRules(long=long_rules, short=short_rules),
        exits=Exits(target_pct=target_pct, stop_pct=stop_pct, trailing_stop_pct=trailing_stop_pct, flip=flip),
        risk=Risk(max_entries_per_day=max_entries_per_day, quantity_lots=quantity_lots),
    )
