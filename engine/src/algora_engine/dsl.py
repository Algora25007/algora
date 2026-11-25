"""Algora strategy DSL: a versioned, no-code rule format validated with pydantic.

A strategy is plain JSON. Version 1 describes intraday rule strategies:

* ``instrument``: what is traded (symbol, exchange, segment, tick size, optional lot-size override)
* ``timeframe``: candle size the rules run on
* ``session``: when the first candle starts and when every position is squared off
* ``entry``: AND-ed conditions for going long and for going short, evaluated on each closed candle
* ``exits``: target %, stop %, trailing stop % (any combination, stop and target together allowed) and flip
* ``risk``: max entries per day and quantity in lots

The JSON schema is exported to ``engine/schema/strategy.v1.json`` (see :func:`json_schema`), so the front end
and API validate the same contract. Older documents are upgraded through :func:`load_spec`.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .instruments import SEGMENT_EXCHANGES, Exchange, Segment

SCHEMA_VERSION = 1
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SYMBOL_RE = r"^[A-Z0-9&-]{1,20}$"

TIMEFRAME_MINUTES: dict[str, int] = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "60m": 60}
Timeframe = Literal["1m", "3m", "5m", "10m", "15m", "30m", "60m"]


def hhmm_to_minutes(v: str) -> int:
    if not isinstance(v, str) or not TIME_RE.match(v):
        raise ValueError(f"time must be HH:MM (24h), got {v!r}")
    h, m = v.split(":")
    return int(h) * 60 + int(m)


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


class PriceOperand(_Strict):
    """A price of the candle being evaluated."""

    kind: Literal["price"] = "price"
    field: Literal["open", "high", "low", "close"] = "close"


class ConstOperand(_Strict):
    kind: Literal["const"] = "const"
    value: float


SessionRef = Literal["first_candle_open", "first_candle_high", "first_candle_low", "first_candle_close", "day_open"]


class SessionOperand(_Strict):
    """A level fixed for the day once the first candle (the one starting at ``session.start``) has closed.

    ``offset_pct`` moves the level by a percentage, e.g. +0.1 for 0.1% above the first candle high.
    """

    kind: Literal["session"] = "session"
    ref: SessionRef
    offset_pct: Annotated[float, Field(ge=-20, le=20)] = 0.0


Operand = Annotated[PriceOperand | ConstOperand | SessionOperand, Field(discriminator="kind")]


class Condition(_Strict):
    op: Literal["crosses_above", "crosses_below", "gt", "gte", "lt", "lte"]
    left: Operand
    right: Operand


class EntryRules(_Strict):
    """Conditions are AND-ed. An empty list disables that side."""

    long: Annotated[list[Condition], Field(max_length=10)] = Field(default_factory=list)
    short: Annotated[list[Condition], Field(max_length=10)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_one_side(self) -> EntryRules:
        if not self.long and not self.short:
            raise ValueError("entry needs at least one long or short condition")
        return self


Pct = Annotated[float, Field(ge=0.05, le=20)]


class Exits(_Strict):
    """Exit rules. Any combination is allowed; an open position is always squared off at session.square_off."""

    target_pct: Pct | None = None
    stop_pct: Pct | None = None
    trailing_stop_pct: Pct | None = None
    flip: bool = Field(default=True, description="Reverse on an opposite entry signal")


class Session(_Strict):
    start: str = Field(default="09:15", description="Start of the first candle of the day (IST)")
    square_off: str = Field(default="15:15", description="Time every open position is closed (IST)")

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


class StrategySpec(_Strict):
    schema_version: Literal[1] = SCHEMA_VERSION
    name: Annotated[str, Field(min_length=1, max_length=80)]
    template: Literal["first_candle_cross", "custom"] = "custom"
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
    def _intraday_only(self) -> StrategySpec:
        if self.instrument.segment is Segment.EQUITY_DELIVERY:
            raise ValueError("schema v1 strategies are intraday (they square off daily); use equity_intraday")
        return self

    @property
    def timeframe_minutes(self) -> int:
        return TIMEFRAME_MINUTES[self.timeframe]


class SpecVersionError(ValueError):
    pass


def load_spec(data: dict[str, Any]) -> StrategySpec:
    """Validate a strategy document, upgrading older schema versions first.

    Documents without ``schema_version`` are rejected rather than guessed.
    """
    if not isinstance(data, dict):
        raise SpecVersionError("strategy must be a JSON object")
    version = data.get("schema_version")
    if version is None:
        raise SpecVersionError("schema_version is required")
    if version != SCHEMA_VERSION:
        raise SpecVersionError(f"unsupported schema_version {version!r}; this engine reads version {SCHEMA_VERSION}")
    return StrategySpec.model_validate(data)


def json_schema() -> dict[str, Any]:
    schema = StrategySpec.model_json_schema()
    schema["$id"] = "https://algora25007.github.io/algora/schema/strategy.v1.json"
    schema["title"] = "Algora strategy (schema v1)"
    return schema
