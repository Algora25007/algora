"""Strategy templates expressed on the DSL. The engine executes only the DSL; templates just build it.

Each template has a pydantic parameter model (its JSON schema and defaults drive the front-end form, see
:func:`catalogue`) and a builder that turns parameters into ordinary DSL rules. Nothing in the trading loop knows
which template produced a strategy.

Times left empty (``start``, ``square_off``) take the exchange default: 09:15 / 15:15 on NSE, BSE, NFO and BFO,
09:00 / 23:00 on MCX. The backtest also caps the square-off at the exchange close (15:30 for index contracts that
expire that day from 7 Dec 2026; the MCX close moves with US daylight saving time).
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .dsl import (
    Condition,
    ConstOperand,
    EntryRules,
    ExitConditions,
    Exits,
    IndicatorOperand,
    Instrument,
    PrevSessionOperand,
    PriceOperand,
    Risk,
    Session,
    SessionOperand,
    SideLevels,
    StrategySpec,
    TimeCondition,
    Timeframe,
    hhmm_to_minutes,
    minutes_to_hhmm,
)
from .instruments import Exchange

Direction = Literal["both", "long", "short"]


def default_times(instrument: Instrument) -> tuple[str, str]:
    """(session start, square-off) defaults for the instrument's exchange."""
    return ("09:00", "23:00") if instrument.exchange is Exchange.MCX else ("09:15", "15:15")


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


# ----- parameter models -------------------------------------------------------------------------------------------

Pct = Annotated[float, Field(ge=0.05, le=20)]
Hhmm = Annotated[str | None, Field(description="HH:MM IST; empty = default")]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: Hhmm = None
    square_off: Hhmm = None
    trailing_stop_pct: Pct | None = None
    max_entries_per_day: Annotated[int, Field(ge=1, le=50)] = 1
    quantity_lots: Annotated[int, Field(ge=1, le=500)] = 1

    @field_validator("start", "square_off", "entry_start", "entry_end", check_fields=False)
    @classmethod
    def _hhmm(cls, v: str | None) -> str | None:
        if v is not None:
            hhmm_to_minutes(v)
        return v

    #: Which stop / target types take a value; for other types the value is ignored (cleared).
    _needs_value: ClassVar[dict[str, set[str]]] = {}

    @model_validator(mode="before")
    @classmethod
    def _clear_unused_values(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for kind, needs in cls._needs_value.items():
            typ = data.get(f"{kind}_type", cls.model_fields[f"{kind}_type"].default)
            if typ not in needs:
                data[f"{kind}_value"] = None
        return data

    def times(self, instrument: Instrument) -> tuple[str, str]:
        s, q = default_times(instrument)
        return self.start or s, self.square_off or q

    def _check_value(self, kind: str, typ: str, value: float | None, needs: set[str]) -> None:
        if typ in needs and value is None:
            raise ValueError(f"{kind}_value is required when {kind}_type is {typ}")
        if typ == "pct" and value is not None and not 0.05 <= value <= 20:
            raise ValueError(f"{kind}_value must be 0.05 to 20 (%)")
        if typ in ("r_multiple", "range_multiple") and value is not None and not 0.1 <= value <= 20:
            raise ValueError(f"{kind}_value must be 0.1 to 20 (multiple)")


def _window(entry_start: str | None, entry_end: str | None, start: str, square_off: str) -> list[TimeCondition]:
    if entry_start is None and entry_end is None:
        return []
    return [TimeCondition(start=entry_start or start, end=entry_end or square_off)]


class FirstCandleCrossParams(_Params):
    timeframe: Timeframe = "15m"
    buffer_pct: Annotated[float, Field(ge=0, le=5)] = 0.0
    direction: Direction = "both"
    target_pct: Pct | None = None
    stop_pct: Pct | None = None
    flip: bool = True


class OpeningRangeBreakoutParams(_Params):
    _needs_value: ClassVar[dict[str, set[str]]] = {"stop": {"pct"}, "target": {"pct", "r_multiple", "range_multiple"}}
    timeframe: Timeframe = "5m"
    opening_range_minutes: Annotated[int, Field(ge=1, le=375, description="Range length from the session start")] = 30
    buffer_type: Literal["pct", "points"] = "pct"
    buffer: Annotated[float, Field(ge=0, le=100_000, description="Added beyond the range high / low")] = 0.0
    direction: Direction = "both"
    range_min_pct: Annotated[float | None, Field(ge=0, le=20, description="Skip days whose range is narrower")] = None
    range_max_pct: Annotated[float | None, Field(ge=0, le=20, description="Skip days whose range is wider")] = None
    stop_type: Literal["none", "pct", "range_opposite", "range_mid"] = "range_opposite"
    stop_value: float | None = None
    target_type: Literal["none", "pct", "r_multiple", "range_multiple"] = "range_multiple"
    target_value: float | None = 1.0
    flip: bool = False
    entry_start: Hhmm = None
    entry_end: Hhmm = None

    @model_validator(mode="after")
    def _check(self) -> OpeningRangeBreakoutParams:
        if self.buffer_type == "pct" and self.buffer > 5:
            raise ValueError("buffer must be 0 to 5 when buffer_type is pct")
        if (
            self.range_min_pct is not None
            and self.range_max_pct is not None
            and self.range_min_pct > self.range_max_pct
        ):
            raise ValueError("range_min_pct must not exceed range_max_pct")
        self._check_value("stop", self.stop_type, self.stop_value, {"pct"})
        self._check_value("target", self.target_type, self.target_value, {"pct", "r_multiple", "range_multiple"})
        if self.target_type == "r_multiple" and self.stop_type == "none":
            raise ValueError("an r_multiple target needs a stop")
        return self


class GapFadeParams(_Params):
    _needs_value: ClassVar[dict[str, set[str]]] = {"stop": {"pct"}, "target": {"pct", "r_multiple"}}
    timeframe: Timeframe = "5m"
    min_gap_pct: Annotated[float, Field(ge=0.05, le=20, description="Smallest gap to fade, % of previous close")] = 0.5
    max_gap_pct: Annotated[float | None, Field(ge=0.05, le=20, description="Skip gaps larger than this")] = 3.0
    fade: Literal["both", "gap_up", "gap_down"] = Field(
        default="both", description="Which gaps to fade: a gap up is faded short, a gap down long"
    )
    require_unfilled: bool = Field(
        default=True, description="Enter only while price is still beyond the previous close (gap not yet filled)"
    )
    entry_start: Hhmm = None
    entry_end: Annotated[
        str | None, Field(description="Last signal-candle close (HH:MM IST); empty = 45 min after start")
    ] = None
    stop_type: Literal["none", "pct", "first_candle_extreme"] = "pct"
    stop_value: float | None = 1.0
    target_type: Literal["none", "pct", "r_multiple", "prev_close"] = "prev_close"
    target_value: float | None = None
    flip: bool = False

    @model_validator(mode="after")
    def _check(self) -> GapFadeParams:
        if self.max_gap_pct is not None and self.max_gap_pct < self.min_gap_pct:
            raise ValueError("max_gap_pct must not be below min_gap_pct")
        self._check_value("stop", self.stop_type, self.stop_value, {"pct"})
        self._check_value("target", self.target_type, self.target_value, {"pct", "r_multiple"})
        if self.target_type == "r_multiple" and self.stop_type == "none":
            raise ValueError("an r_multiple target needs a stop")
        return self


class MomentumParams(_Params):
    _needs_value: ClassVar[dict[str, set[str]]] = {"stop": {"pct"}, "target": {"pct", "r_multiple"}}
    timeframe: Timeframe = "5m"
    signal: Literal["ema_cross", "breakout"] = "ema_cross"
    fast_length: Annotated[int, Field(ge=1, le=500)] = 9
    slow_length: Annotated[int, Field(ge=2, le=500)] = 21
    breakout_length: Annotated[int, Field(ge=1, le=500, description="Candles in the N-bar breakout")] = 20
    breakout_scope: Literal["session", "continuous"] = Field(
        default="session", description="session: only today's candles count toward the N-bar high/low"
    )
    vwap_filter: bool = Field(default=False, description="Longs only above session VWAP, shorts only below")
    direction: Direction = "both"
    exit_on_opposite_signal: bool = True
    stop_type: Literal["none", "pct"] = "pct"
    stop_value: float | None = 1.0
    target_type: Literal["none", "pct", "r_multiple"] = "none"
    target_value: float | None = None
    flip: bool = True
    max_entries_per_day: Annotated[int, Field(ge=1, le=50)] = 3
    entry_start: Hhmm = None
    entry_end: Hhmm = None

    @model_validator(mode="after")
    def _check(self) -> MomentumParams:
        if self.signal == "ema_cross" and self.fast_length >= self.slow_length:
            raise ValueError("fast_length must be shorter than slow_length")
        self._check_value("stop", self.stop_type, self.stop_value, {"pct"})
        self._check_value("target", self.target_type, self.target_value, {"pct", "r_multiple"})
        if self.target_type == "r_multiple" and self.stop_type == "none":
            raise ValueError("an r_multiple target needs a stop")
        return self


# ----- builders ---------------------------------------------------------------------------------------------------

CLOSE = PriceOperand(field="close")


def _level(ref: str, pct: float = 0.0, points: float = 0.0) -> SessionOperand:
    return SessionOperand(ref=ref, offset_pct=pct + 0.0, offset_points=points + 0.0)  # type: ignore[arg-type]


def _sides(direction: str, long_rules: list, short_rules: list) -> EntryRules:
    return EntryRules(
        long=long_rules if direction in ("both", "long") else [],
        short=short_rules if direction in ("both", "short") else [],
    )


def build_first_candle_cross(name: str, instrument: Instrument, p: FirstCandleCrossParams) -> StrategySpec:
    start, square_off = p.times(instrument)
    spec = first_candle_cross(
        name=name,
        instrument=instrument,
        timeframe=p.timeframe,
        buffer_pct=p.buffer_pct,
        start=start,
        square_off=square_off,
        target_pct=p.target_pct,
        stop_pct=p.stop_pct,
        trailing_stop_pct=p.trailing_stop_pct,
        flip=p.flip,
        max_entries_per_day=p.max_entries_per_day,
        quantity_lots=p.quantity_lots,
        direction=p.direction,
    )
    full = p.model_dump(mode="json") | {"start": start, "square_off": square_off}
    return spec.model_copy(update={"template_params": full})


def build_opening_range_breakout(name: str, instrument: Instrument, p: OpeningRangeBreakoutParams) -> StrategySpec:
    """Opening Range Breakout.

    The opening range is the high and low of the candles in the first ``opening_range_minutes`` from the session
    start. Go long when a candle closes above the range high plus the buffer (after the previous candle closed at
    or below it); go short on the mirror-image cross below the range low minus the buffer. Optional filters skip
    days whose range width (% of the price at the end of the range) is outside ``range_min_pct``..``range_max_pct``.
    """
    start, square_off = p.times(instrument)
    pct = p.buffer if p.buffer_type == "pct" else 0.0
    pts = p.buffer if p.buffer_type == "points" else 0.0
    up = _level("range_high", pct, pts)
    dn = _level("range_low", -pct, -pts)
    width = SessionOperand(ref="range_width_pct")
    filters: list[Any] = []
    if p.range_min_pct is not None:
        filters.append(Condition(op="gte", left=width, right=ConstOperand(value=p.range_min_pct)))
    if p.range_max_pct is not None:
        filters.append(Condition(op="lte", left=width, right=ConstOperand(value=p.range_max_pct)))
    filters += _window(p.entry_start, p.entry_end, start, square_off)
    entry = _sides(
        p.direction,
        [Condition(op="crosses_above", left=CLOSE, right=up), *filters],
        [Condition(op="crosses_below", left=CLOSE, right=dn), *filters],
    )
    stop_level = None
    if p.stop_type == "range_opposite":
        stop_level = SideLevels(long=_level("range_low"), short=_level("range_high"))
    elif p.stop_type == "range_mid":
        stop_level = SideLevels(long=_level("range_mid"), short=_level("range_mid"))
    exits = Exits(
        stop_pct=p.stop_value if p.stop_type == "pct" else None,
        stop_level=stop_level,
        target_pct=p.target_value if p.target_type == "pct" else None,
        target_r_multiple=p.target_value if p.target_type == "r_multiple" else None,
        target_range_multiple=p.target_value if p.target_type == "range_multiple" else None,
        trailing_stop_pct=p.trailing_stop_pct,
        flip=p.flip,
    )
    return StrategySpec(
        name=name,
        template="opening_range_breakout",
        template_params=p.model_dump(mode="json") | {"start": start, "square_off": square_off},
        instrument=instrument,
        timeframe=p.timeframe,
        session=Session(start=start, square_off=square_off, opening_range_minutes=p.opening_range_minutes),
        entry=entry,
        exits=exits,
        risk=Risk(max_entries_per_day=p.max_entries_per_day, quantity_lots=p.quantity_lots),
    )


def build_gap_fade(name: str, instrument: Instrument, p: GapFadeParams) -> StrategySpec:
    """Gap fade.

    The gap is the day's open (first candle at or after the session start) against the previous trading session's
    close, in %. A gap up of ``min_gap_pct``..``max_gap_pct`` is faded short and a gap down of the same size long,
    from the first candle's close until ``entry_end``. With ``require_unfilled`` the candle must still close beyond
    the previous close. The default target is the previous close (the gap filled).
    """
    start, square_off = p.times(instrument)
    entry_end = p.entry_end or minutes_to_hhmm(min(hhmm_to_minutes(start) + 45, hhmm_to_minutes(square_off)))
    gap = SessionOperand(ref="gap_pct")
    prev_close = PrevSessionOperand(field="close")
    window = [TimeCondition(start=p.entry_start or start, end=entry_end)]
    short_rules: list[Any] = [Condition(op="gte", left=gap, right=ConstOperand(value=p.min_gap_pct))]
    long_rules: list[Any] = [Condition(op="lte", left=gap, right=ConstOperand(value=-p.min_gap_pct))]
    if p.max_gap_pct is not None:
        short_rules.append(Condition(op="lte", left=gap, right=ConstOperand(value=p.max_gap_pct)))
        long_rules.append(Condition(op="gte", left=gap, right=ConstOperand(value=-p.max_gap_pct)))
    if p.require_unfilled:
        short_rules.append(Condition(op="gt", left=CLOSE, right=prev_close))
        long_rules.append(Condition(op="lt", left=CLOSE, right=prev_close))
    direction = {"both": "both", "gap_up": "short", "gap_down": "long"}[p.fade]
    entry = _sides(direction, [*long_rules, *window], [*short_rules, *window])
    stop_level = None
    if p.stop_type == "first_candle_extreme":
        stop_level = SideLevels(long=_level("first_candle_low"), short=_level("first_candle_high"))
    exits = Exits(
        stop_pct=p.stop_value if p.stop_type == "pct" else None,
        stop_level=stop_level,
        target_pct=p.target_value if p.target_type == "pct" else None,
        target_r_multiple=p.target_value if p.target_type == "r_multiple" else None,
        target_level=SideLevels(long=prev_close, short=prev_close) if p.target_type == "prev_close" else None,
        trailing_stop_pct=p.trailing_stop_pct,
        flip=p.flip,
    )
    return StrategySpec(
        name=name,
        template="gap_fade",
        template_params=p.model_dump(mode="json") | {"start": start, "square_off": square_off, "entry_end": entry_end},
        instrument=instrument,
        timeframe=p.timeframe,
        session=Session(start=start, square_off=square_off, signals_from="session_start"),
        entry=entry,
        exits=exits,
        risk=Risk(max_entries_per_day=p.max_entries_per_day, quantity_lots=p.quantity_lots),
    )


def build_momentum(name: str, instrument: Instrument, p: MomentumParams) -> StrategySpec:
    """Intraday momentum.

    ``ema_cross``: long when the fast EMA of the close crosses above the slow EMA, short on the cross below (EMAs
    run continuously across days). ``breakout``: long when the close crosses above the highest high of the previous
    ``breakout_length`` candles, short when it crosses below their lowest low. ``vwap_filter`` adds close above /
    below the session VWAP. With ``exit_on_opposite_signal`` a position also closes on the opposite crossover or
    breakout (without the VWAP filter), whichever sides are traded.
    """
    start, square_off = p.times(instrument)
    if p.signal == "ema_cross":
        fast = IndicatorOperand(name="ema", length=p.fast_length)
        slow = IndicatorOperand(name="ema", length=p.slow_length)
        up = Condition(op="crosses_above", left=fast, right=slow)
        down = Condition(op="crosses_below", left=fast, right=slow)
    else:
        hi = IndicatorOperand(name="highest", length=p.breakout_length, field="high", scope=p.breakout_scope)
        lo = IndicatorOperand(name="lowest", length=p.breakout_length, field="low", scope=p.breakout_scope)
        up = Condition(op="crosses_above", left=CLOSE, right=hi)
        down = Condition(op="crosses_below", left=CLOSE, right=lo)
    long_rules: list[Any] = [up]
    short_rules: list[Any] = [down]
    if p.vwap_filter:
        vwap = IndicatorOperand(name="vwap")
        long_rules.append(Condition(op="gt", left=CLOSE, right=vwap))
        short_rules.append(Condition(op="lt", left=CLOSE, right=vwap))
    window = _window(p.entry_start, p.entry_end, start, square_off)
    entry = _sides(p.direction, [*long_rules, *window], [*short_rules, *window])
    conditions = None
    if p.exit_on_opposite_signal:
        conditions = ExitConditions(
            long=[down] if p.direction in ("both", "long") else [],
            short=[up] if p.direction in ("both", "short") else [],
        )
    exits = Exits(
        stop_pct=p.stop_value if p.stop_type == "pct" else None,
        target_pct=p.target_value if p.target_type == "pct" else None,
        target_r_multiple=p.target_value if p.target_type == "r_multiple" else None,
        trailing_stop_pct=p.trailing_stop_pct,
        flip=p.flip,
        conditions=conditions,
    )
    return StrategySpec(
        name=name,
        template="momentum",
        template_params=p.model_dump(mode="json") | {"start": start, "square_off": square_off},
        instrument=instrument,
        timeframe=p.timeframe,
        session=Session(start=start, square_off=square_off),
        entry=entry,
        exits=exits,
        risk=Risk(max_entries_per_day=p.max_entries_per_day, quantity_lots=p.quantity_lots),
    )


# ----- catalogue --------------------------------------------------------------------------------------------------


class TemplateUnavailable(ValueError):
    pass


_TEMPLATES: dict[str, dict[str, Any]] = {
    "first_candle_cross": {
        "name": "First Candle Cross",
        "description": "Trade the close beyond the first candle's high or low, with an optional buffer.",
        "params": FirstCandleCrossParams,
        "build": build_first_candle_cross,
    },
    "opening_range_breakout": {
        "name": "Opening Range Breakout",
        "description": "Let the opening range form, then follow the side that breaks first (on a candle close).",
        "params": OpeningRangeBreakoutParams,
        "build": build_opening_range_breakout,
    },
    "gap_fade": {
        "name": "Gap fade",
        "description": "Fade an opening gap against the previous session's close, back toward that close.",
        "params": GapFadeParams,
        "build": build_gap_fade,
    },
    "momentum": {
        "name": "Momentum",
        "description": "Intraday momentum: EMA crossover or N-candle breakout, with an optional VWAP filter.",
        "params": MomentumParams,
        "build": build_momentum,
    },
}
UNAVAILABLE: dict[str, dict[str, str]] = {
    "monthly_option_spread": {
        "name": "Monthly option spread",
        "description": "A defined-risk spread on the monthly expiry.",
        "reason": "Not available yet: it needs option-chain data (strikes, premiums, expiries) the engine does not "
        "have.",
    }
}
TEMPLATE_IDS = tuple(_TEMPLATES)
TEMPLATE_NAMES = {k: v["name"] for k, v in {**_TEMPLATES, **UNAVAILABLE}.items()}


def params_model(template_id: str) -> type[_Params]:
    if template_id in UNAVAILABLE:
        raise TemplateUnavailable(UNAVAILABLE[template_id]["reason"])
    if template_id not in _TEMPLATES:
        raise KeyError(template_id)
    return _TEMPLATES[template_id]["params"]


def build_template(template_id: str, *, name: str, instrument: Instrument, params: dict[str, Any]) -> StrategySpec:
    """Validate ``params`` for a template and build its DSL strategy. Raises pydantic ValidationError / ValueError."""
    model = params_model(template_id)
    p = model.model_validate(params)
    return _TEMPLATES[template_id]["build"](name, instrument, p)


def catalogue() -> list[dict[str, Any]]:
    """Every template with its parameter JSON schema and defaults, for the front end's forms."""
    out: list[dict[str, Any]] = []
    for tid, t in _TEMPLATES.items():
        model = t["params"]
        out.append(
            {
                "id": tid,
                "name": t["name"],
                "description": t["description"],
                "available": True,
                "parameters": model.model_json_schema(),
                "defaults": model().model_dump(mode="json"),
                "exchange_defaults": {
                    "NSE/BSE/NFO/BFO": {"start": "09:15", "square_off": "15:15"},
                    "MCX": {"start": "09:00", "square_off": "23:00"},
                },
            }
        )
    for tid, t in UNAVAILABLE.items():
        out.append({"id": tid, **t, "available": False, "parameters": None, "defaults": None})
    return out
