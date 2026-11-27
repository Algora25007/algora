"""Algora strategy DSL: a versioned, no-code rule format validated with pydantic.

A strategy is plain JSON. Version 2 describes intraday rule strategies:

* ``instrument``: what is traded (symbol, exchange, segment, tick size, optional lot-size override)
* ``timeframe``: candle size the rules run on
* ``session``: when the first candle starts, the opening-range window, when rules start being evaluated, and when
  every position is squared off
* ``entry``: AND-ed conditions for going long and for going short, evaluated on each closed candle. A condition
  compares two operands (``gt``, ``crosses_above``, ...) or limits the time of day (``time_between``)
* ``exits``: stop (% or a level such as the other end of the opening range), target (%, a level, a multiple of the
  initial risk or of the opening range), trailing stop %, flip and rule-based exit conditions
* ``risk``: max entries per day and quantity in lots

Operands are candle prices, constants, session levels (first candle, opening range, day open, and the derived
``range_width_pct`` / ``gap_pct``), previous-session values and indicators (EMA, SMA, highest/lowest of the
previous N candles, session VWAP). A condition whose operand is not available yet (range still forming, indicator
warming up, no previous session in the data) is false.

The JSON schema is exported to ``engine/schema/strategy.v2.json`` (see :func:`json_schema`), so the front end
and API validate the same contract. Older documents are upgraded through :func:`load_spec` / :func:`upgrade`.
Schema history: v1 (Phase 1) had first-candle levels only; v2 is a strict superset of v1, so a v1 document
upgrades by changing ``schema_version`` alone and behaves identically.
"""

from __future__ import annotations

import copy
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, field_validator, model_validator

from .instruments import SEGMENT_EXCHANGES, Exchange, Segment

SCHEMA_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SYMBOL_RE = r"^[A-Z0-9&-]{1,20}$"

TIMEFRAME_MINUTES: dict[str, int] = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "60m": 60}
Timeframe = Literal["1m", "3m", "5m", "10m", "15m", "30m", "60m"]
PriceField = Literal["open", "high", "low", "close"]


def hhmm_to_minutes(v: str) -> int:
    if not isinstance(v, str) or not TIME_RE.match(v):
        raise ValueError(f"time must be HH:MM (24h), got {v!r}")
    h, m = v.split(":")
    return int(h) * 60 + int(m)


def minutes_to_hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Instrument(_Strict):
    symbol: Annotated[str, Field(pattern=SYMBOL_RE, description="Exchange trading symbol, e.g. NIFTY")]
    exchange: Exchange
    segment: Segment
    tick_size: Annotated[float, Field(gt=0, le=100)] = 0.05
    lot_size: Annotated[int | None, Field(ge=1, le=1_000_000)] = Field(
        default=None, description="Override the lot-size table (units per lot). Required for stock F&O."
    )
    agri: bool = Field(default=False, description="Agricultural commodity (CTT exempt, lower SEBI fee)")

    @model_validator(mode="after")
    def _segment_matches_exchange(self) -> Instrument:
        allowed = SEGMENT_EXCHANGES[self.segment]
        if self.exchange not in allowed:
            names = ", ".join(sorted(e.value for e in allowed))
            raise ValueError(f"segment {self.segment.value} trades on {names}, not {self.exchange.value}")
        if self.agri and self.exchange is not Exchange.MCX:
            raise ValueError("agri applies only to MCX commodities")
        return self

    @property
    def is_commodity(self) -> bool:
        return self.segment in (Segment.COMMODITY_FUTURES, Segment.COMMODITY_OPTIONS)


# ----- operands ---------------------------------------------------------------------------------------------------


class PriceOperand(_Strict):
    """A price of the candle being evaluated."""

    kind: Literal["price"] = "price"
    field: PriceField = "close"


class ConstOperand(_Strict):
    kind: Literal["const"] = "const"
    value: float


#: Price levels fixed for the day. ``first_candle_*`` and ``day_open`` are known once the first candle (the first
#: one starting at or after ``session.start``) has closed; ``range_*`` once the opening range has formed.
LEVEL_REFS = (
    "first_candle_open",
    "first_candle_high",
    "first_candle_low",
    "first_candle_close",
    "day_open",
    "range_open",
    "range_high",
    "range_low",
    "range_mid",
    "range_close",
)
#: Derived numbers (not prices): ``range_width_pct`` = (range high - range low) / range close x 100;
#: ``gap_pct`` = (day open - previous session close) / previous session close x 100.
METRIC_REFS = ("range_width_pct", "gap_pct")
SessionRef = Literal[
    "first_candle_open",
    "first_candle_high",
    "first_candle_low",
    "first_candle_close",
    "day_open",
    "range_open",
    "range_high",
    "range_low",
    "range_mid",
    "range_close",
    "range_width_pct",
    "gap_pct",
]
OffsetPct = Annotated[float, Field(ge=-20, le=20)]
OffsetPoints = Annotated[float, Field(ge=-100_000, le=100_000)]


class SessionOperand(_Strict):
    """A value fixed for the day: a first-candle / opening-range level or a derived session metric.

    ``offset_pct`` moves a level by a percentage (e.g. +0.1 for 0.1% above the range high) and ``offset_points``
    by an absolute amount; both may be combined (percentage first). Metrics take no offset.
    """

    kind: Literal["session"] = "session"
    ref: SessionRef
    offset_pct: OffsetPct = 0.0
    offset_points: OffsetPoints = 0.0

    @model_validator(mode="after")
    def _no_offset_on_metric(self) -> SessionOperand:
        if self.ref in METRIC_REFS and (self.offset_pct or self.offset_points):
            raise ValueError(f"{self.ref} is a percentage, not a price level; it takes no offset")
        return self


class PrevSessionOperand(_Strict):
    """A value of the previous trading session (open, high, low or close over all of that day's candles).

    The previous session is the latest earlier day in the candle data, and it counts only when the exchange
    calendar has no trading day between it and today (a missing day makes the value unavailable).
    """

    kind: Literal["prev_session"] = "prev_session"
    field: PriceField = "close"
    offset_pct: OffsetPct = 0.0
    offset_points: OffsetPoints = 0.0


IndicatorName = Literal["ema", "sma", "highest", "lowest", "vwap"]


class IndicatorOperand(_Strict):
    """An indicator value at the close of the candle being evaluated.

    * ``ema`` / ``sma``: moving averages of ``field`` over ``length`` candles (EMA seeded with the SMA of the first
      ``length`` candles, then alpha = 2 / (length + 1)).
    * ``highest`` / ``lowest``: the highest / lowest ``field`` of the ``length`` candles *before* this one (so
      ``close crosses_above highest(high, 20)`` is a 20-candle breakout).
    * ``vwap``: session VWAP of the typical price (high + low + close) / 3, weighted by volume, from the day's first
      candle. Needs volume on every candle.

    ``scope``: ``continuous`` carries the indicator across days; ``session`` restarts it each day (``vwap`` is always
    ``session``). Indicators see every candle of an open trading day, including candles outside the trading window.
    """

    kind: Literal["indicator"] = "indicator"
    name: IndicatorName
    length: Annotated[int | None, Field(ge=1, le=500)] = None
    field: PriceField = "close"
    scope: Literal["continuous", "session"] = "continuous"

    @model_validator(mode="before")
    @classmethod
    def _vwap_scope(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("name") == "vwap" and "scope" not in data:
            data = {**data, "scope": "session"}
        return data

    @model_validator(mode="after")
    def _shape(self) -> IndicatorOperand:
        if self.name == "vwap":
            if self.length is not None:
                raise ValueError("vwap takes no length")
            if self.field != "close":
                raise ValueError("vwap uses the typical price; it takes no field")
            if self.scope != "session":
                raise ValueError("vwap is a session indicator")
        elif self.length is None:
            raise ValueError(f"{self.name} needs a length")
        return self

    @property
    def key(self) -> tuple:
        return (self.name, self.length, self.field, self.scope)


Operand = Annotated[
    PriceOperand | ConstOperand | SessionOperand | PrevSessionOperand | IndicatorOperand, Field(discriminator="kind")
]
LevelOperand = Annotated[SessionOperand | PrevSessionOperand, Field(discriminator="kind")]


# ----- conditions -------------------------------------------------------------------------------------------------


class Condition(_Strict):
    """Compare two operands on the closed candle. ``crosses_*`` also look at the previous candle of the same day."""

    op: Literal["crosses_above", "crosses_below", "gt", "gte", "lt", "lte"]
    left: Operand
    right: Operand


class TimeCondition(_Strict):
    """True when the candle being evaluated *closes* between ``start`` and ``end`` (IST, both inclusive).

    Orders from a signal are placed at that close (they fill at the next candle's open), so this is the window in
    which orders may be placed.
    """

    op: Literal["time_between"] = "time_between"
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        hhmm_to_minutes(v)
        return v

    @model_validator(mode="after")
    def _order(self) -> TimeCondition:
        if hhmm_to_minutes(self.end) < hhmm_to_minutes(self.start):
            raise ValueError("time_between end must not be before start")
        return self


def _condition_tag(v: Any) -> str:
    op = v.get("op") if isinstance(v, dict) else getattr(v, "op", None)
    return "time" if op == "time_between" else "compare"


AnyCondition = Annotated[
    Annotated[Condition, Tag("compare")] | Annotated[TimeCondition, Tag("time")],
    Discriminator(_condition_tag),
]


class EntryRules(_Strict):
    """Conditions are AND-ed. An empty list disables that side."""

    long: Annotated[list[AnyCondition], Field(max_length=10)] = Field(default_factory=list)
    short: Annotated[list[AnyCondition], Field(max_length=10)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_one_side(self) -> EntryRules:
        if not self.long and not self.short:
            raise ValueError("entry needs at least one long or short condition")
        return self


# ----- exits ------------------------------------------------------------------------------------------------------

Pct = Annotated[float, Field(ge=0.05, le=20)]
Multiple = Annotated[float, Field(ge=0.1, le=20)]


class SideLevels(_Strict):
    """A price level per side, e.g. a long stop at the range low and a short stop at the range high."""

    long: LevelOperand | None = None
    short: LevelOperand | None = None

    @model_validator(mode="after")
    def _levels_only(self) -> SideLevels:
        for op in (self.long, self.short):
            if isinstance(op, SessionOperand) and op.ref in METRIC_REFS:
                raise ValueError(f"{op.ref} is not a price level")
        if self.long is None and self.short is None:
            raise ValueError("give a level for long, short or both")
        return self


class ExitConditions(_Strict):
    """Close an open position at the next candle's open when all of its side's conditions are true on a close.

    ``long`` closes a long position, ``short`` a short one. Same condition types as entry rules.
    """

    long: Annotated[list[AnyCondition], Field(max_length=10)] = Field(default_factory=list)
    short: Annotated[list[AnyCondition], Field(max_length=10)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_one_side(self) -> ExitConditions:
        if not self.long and not self.short:
            raise ValueError("exit conditions need at least one long or short condition")
        return self


class Exits(_Strict):
    """Exit rules. An open position is always squared off at session.square_off (or the exchange close if earlier).

    At most one stop (``stop_pct`` or ``stop_level``) and one target (``target_pct``, ``target_level``,
    ``target_r_multiple`` or ``target_range_multiple``). The trailing stop can be added to either stop. Levels are
    read once, when the entry fills, and stay fixed for the trade.
    """

    target_pct: Pct | None = None
    stop_pct: Pct | None = None
    trailing_stop_pct: Pct | None = None
    flip: bool = Field(default=True, description="Reverse on an opposite entry signal")
    stop_level: SideLevels | None = Field(default=None, description="Stop at a session / previous-session level")
    target_level: SideLevels | None = Field(default=None, description="Target at a session / previous-session level")
    target_r_multiple: Multiple | None = Field(
        default=None, description="Target = entry ± this multiple of the initial risk (entry to stop)"
    )
    target_range_multiple: Multiple | None = Field(
        default=None, description="Target = entry ± this multiple of the opening-range width (high - low)"
    )
    conditions: ExitConditions | None = Field(default=None, description="Rule-based exits, e.g. an opposite cross")

    @model_validator(mode="after")
    def _one_of_each(self) -> Exits:
        if self.stop_pct is not None and self.stop_level is not None:
            raise ValueError("use stop_pct or stop_level, not both")
        targets = [self.target_pct, self.target_level, self.target_r_multiple, self.target_range_multiple]
        if sum(t is not None for t in targets) > 1:
            raise ValueError("use only one of target_pct, target_level, target_r_multiple, target_range_multiple")
        if self.target_r_multiple is not None and self.stop_pct is None and self.stop_level is None:
            raise ValueError("target_r_multiple needs a stop (stop_pct or stop_level)")
        return self


class Session(_Strict):
    start: str = Field(default="09:15", description="Start of the first candle of the day (IST)")
    square_off: str = Field(default="15:15", description="Time every open position is closed (IST)")
    opening_range_minutes: Annotated[int | None, Field(ge=1, le=600)] = Field(
        default=None,
        description="Opening range length from the first candle; a multiple of the timeframe. None = the first candle.",
    )
    signals_from: Literal["after_opening_range", "session_start"] = Field(
        default="after_opening_range",
        description="Evaluate entry rules from the candle after the opening range (v1 behaviour), or from the first "
        "candle of the day",
    )

    @field_validator("start", "square_off")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        hhmm_to_minutes(v)
        return v

    @model_validator(mode="after")
    def _order(self) -> Session:
        if hhmm_to_minutes(self.square_off) <= hhmm_to_minutes(self.start):
            raise ValueError("square_off must be after start")
        return self

    @property
    def start_minute(self) -> int:
        return hhmm_to_minutes(self.start)

    @property
    def square_off_minute(self) -> int:
        return hhmm_to_minutes(self.square_off)


class Risk(_Strict):
    max_entries_per_day: Annotated[int, Field(ge=1, le=50)] = 1
    quantity_lots: Annotated[int, Field(ge=1, le=500)] = 1


TemplateId = Literal["first_candle_cross", "opening_range_breakout", "gap_fade", "momentum", "custom"]


class StrategySpec(_Strict):
    schema_version: Literal[2] = SCHEMA_VERSION
    name: Annotated[str, Field(min_length=1, max_length=80)]
    template: TemplateId = "custom"
    template_params: dict[str, Any] = Field(default_factory=dict, description="UI round-trip only; not executed")
    instrument: Instrument
    timeframe: Timeframe
    session: Session = Field(default_factory=Session)
    entry: EntryRules
    exits: Exits = Field(default_factory=Exits)
    risk: Risk = Field(default_factory=Risk)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @model_validator(mode="after")
    def _checks(self) -> StrategySpec:
        if self.instrument.segment is Segment.EQUITY_DELIVERY:
            raise ValueError("strategies are intraday (they square off daily); use equity_intraday")
        orm = self.session.opening_range_minutes
        if orm is not None:
            if orm % self.timeframe_minutes:
                raise ValueError(f"opening_range_minutes ({orm}) must be a multiple of the {self.timeframe} timeframe")
            if self.session.start_minute + orm >= self.session.square_off_minute:
                raise ValueError("the opening range must end before square_off")
        return self

    @property
    def timeframe_minutes(self) -> int:
        return TIMEFRAME_MINUTES[self.timeframe]

    @property
    def opening_range_length(self) -> int:
        """Opening range length in minutes (one candle when not set)."""
        return self.session.opening_range_minutes or self.timeframe_minutes

    def operands(self) -> list[Any]:
        """Every operand the strategy reads: entry conditions and exit levels."""
        out: list[Any] = []
        conds = [*self.entry.long, *self.entry.short]
        if self.exits.conditions is not None:
            conds += [*self.exits.conditions.long, *self.exits.conditions.short]
        for c in conds:
            if isinstance(c, Condition):
                out += [c.left, c.right]
        for lv in (self.exits.stop_level, self.exits.target_level):
            if lv is not None:
                out += [o for o in (lv.long, lv.short) if o is not None]
        return out

    def uses_prev_session(self) -> bool:
        return any(
            isinstance(o, PrevSessionOperand) or (isinstance(o, SessionOperand) and o.ref == "gap_pct")
            for o in self.operands()
        )

    def uses_vwap(self) -> bool:
        return any(isinstance(o, IndicatorOperand) and o.name == "vwap" for o in self.operands())


class SpecVersionError(ValueError):
    pass


def upgrade(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a strategy document to the current schema version without validating it.

    v1 -> v2: v2 only adds optional fields and operand kinds, with defaults equal to v1 behaviour, so the upgrade
    sets ``schema_version`` to 2 and leaves everything else as it was.
    """
    if not isinstance(data, dict):
        raise SpecVersionError("strategy must be a JSON object")
    version = data.get("schema_version")
    if version is None:
        raise SpecVersionError("schema_version is required")
    if isinstance(version, bool) or version not in SUPPORTED_VERSIONS:
        raise SpecVersionError(
            f"unsupported schema_version {version!r}; this engine reads versions "
            + ", ".join(map(str, SUPPORTED_VERSIONS))
        )
    doc = copy.deepcopy(data)
    if version == 1:
        doc["schema_version"] = 2
    return doc


def load_spec(data: dict[str, Any]) -> StrategySpec:
    """Validate a strategy document, upgrading older schema versions first.

    Documents without ``schema_version`` are rejected rather than guessed.
    """
    return StrategySpec.model_validate(upgrade(data))


def json_schema() -> dict[str, Any]:
    schema = StrategySpec.model_json_schema()
    schema["$id"] = f"https://algora25007.github.io/algora/schema/strategy.v{SCHEMA_VERSION}.json"
    schema["title"] = f"Algora strategy (schema v{SCHEMA_VERSION})"
    return schema
