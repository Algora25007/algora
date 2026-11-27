"""IST exchange calendar, ported from the browser's algora-dash.js.

All times are Indian Standard Time wall-clock, expressed as minutes after midnight.

Rules (same as the front end):

* NSE/BSE holidays come from ``data/holidays.json`` (2026: NSE/CMTR/71775, NSE/FAOP/71777, NSE/CMTR/72260).
  Years not in the file are "unknown": weekdays are treated as open and :func:`is_known_year` returns False.
* NSE cash: pre-open 09:00-09:15, normal 09:15-15:30.
* NSE F&O: 09:15-15:40 until 4 Dec 2026; 09:15-15:45 from 7 Dec 2026 (SEBI circular of 15 Oct 2026,
  NSE/FAOP/77356). From 7 Dec 2026, index contracts expiring that day stop trading at 15:30.
* MCX: 09:00 to 23:30 while the US is on daylight saving time (2nd Sunday of March to 1st Sunday of
  November), 23:55 otherwise. On an NSE holiday MCX runs only the evening session from 17:00, unless the
  day is a full MCX holiday.
* Expiries: NSE contracts on Tuesday (Nifty weekly; other NSE contracts monthly, the last Tuesday);
  BSE contracts on Thursday (Sensex weekly; others monthly, the last Thursday). A holiday moves the expiry to
  the previous trading day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from importlib import resources

from .instruments import BSE_INDEX_SYMBOLS, NSE_INDEX_SYMBOLS, Segment

NSE_PRE_OPEN = (9 * 60, 9 * 60 + 15)
NSE_CASH = (9 * 60 + 15, 15 * 60 + 30)
FNO_OPEN = 9 * 60 + 15
FNO_CLOSE_OLD = 15 * 60 + 40
FNO_CLOSE_NEW = 15 * 60 + 45
EXPIRING_INDEX_CLOSE = 15 * 60 + 30
#: First trading day of the new F&O timings.
FNO_TIMING_CHANGE = date(2026, 12, 7)
MCX_OPEN = 9 * 60
MCX_EVENING_OPEN = 17 * 60
MCX_CLOSE_US_DST = 23 * 60 + 30
MCX_CLOSE_US_STD = 23 * 60 + 55


@dataclass(frozen=True)
class HolidayData:
    nse: dict[date, str]
    mcx_full: frozenset[date]
    known_years: frozenset[int]


@lru_cache(maxsize=1)
def load_holidays() -> HolidayData:
    raw = json.loads(resources.files("algora_engine").joinpath("data/holidays.json").read_text("utf-8"))
    nse: dict[date, str] = {}
    mcx: set[date] = set()
    years: set[int] = set()
    for y, block in raw["years"].items():
        years.add(int(y))
        for k, name in block["nse"].items():
            nse[date.fromisoformat(k)] = name
        for k in block.get("mcx_closed_full_day", []):
            mcx.add(date.fromisoformat(k))
    return HolidayData(nse=nse, mcx_full=frozenset(mcx), known_years=frozenset(years))


def is_known_year(year: int) -> bool:
    return year in load_holidays().known_years


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def holiday_name(d: date) -> str | None:
    return load_holidays().nse.get(d)


def is_nse_trading_day(d: date) -> bool:
    return not is_weekend(d) and d not in load_holidays().nse


def prev_trading_day(d: date) -> date:
    """The latest NSE trading day on or before ``d``."""
    x = d
    for _ in range(15):
        if is_nse_trading_day(x):
            return x
        x -= timedelta(days=1)
    raise ValueError(f"no trading day within 15 days before {d}")  # pragma: no cover - impossible with real data


def next_trading_day(d: date) -> date:
    """The earliest NSE trading day strictly after ``d``."""
    x = d + timedelta(days=1)
    for _ in range(15):
        if is_nse_trading_day(x):
            return x
        x += timedelta(days=1)
    raise ValueError(f"no trading day within 15 days after {d}")  # pragma: no cover


def is_session_day(d: date, segment: Segment) -> bool:
    """Whether the exchange that ``segment`` trades on is open on ``d`` (MCX: any weekday that is not a full MCX
    holiday, including evening-only days; NSE/BSE: NSE trading days). Unknown years count weekdays as open."""
    if segment in (Segment.COMMODITY_FUTURES, Segment.COMMODITY_OPTIONS):
        return not is_weekend(d) and d not in load_holidays().mcx_full
    return is_nse_trading_day(d)


def previous_session_check(prev: date, today: date, segment: Segment) -> str:
    """Is ``prev`` (the latest earlier day in the candle data) the previous trading session before ``today``?

    Returns ``"ok"``; ``"missing"`` when a known trading day lies between them (data has a hole); or
    ``"unverified"`` when only days of a year without a holiday list lie between them and one of those is a
    weekday (it may have been a holiday, or the data may be missing it).
    """
    x = prev + timedelta(days=1)
    unverified = False
    while x < today:
        if is_session_day(x, segment):
            if is_known_year(x.year):
                return "missing"
            unverified = True
        x += timedelta(days=1)
    return "unverified" if unverified else "ok"


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    add = (6 - first.weekday()) % 7  # weekday(): Monday=0 .. Sunday=6
    return first + timedelta(days=add + 7 * (n - 1))


def us_dst(d: date) -> bool:
    """True when ``d`` falls in US daylight saving time, as the front end computes it.

    The start Sunday itself counts as standard time and the end Sunday as DST (both are weekends,
    so MCX sessions are unaffected).
    """
    return _nth_sunday(d.year, 3, 2) < d <= _nth_sunday(d.year, 11, 1)


def mcx_close(d: date) -> int:
    return MCX_CLOSE_US_DST if us_dst(d) else MCX_CLOSE_US_STD


@dataclass(frozen=True)
class Expiry:
    exchange: str  # "NSE" or "BSE"
    index: str  # "NIFTY" or "SENSEX"
    monthly: bool


def expiries_for(d: date) -> list[Expiry]:
    """Index expiries that settle on trading day ``d`` (Nifty weekly/monthly, Sensex weekly/monthly)."""
    out: list[Expiry] = []
    for weekday, exch, idx in ((1, "NSE", "NIFTY"), (3, "BSE", "SENSEX")):
        for i in range(7):
            nominal = d + timedelta(days=i)
            if nominal.weekday() != weekday:
                continue
            if prev_trading_day(nominal) == d:
                monthly = (nominal + timedelta(days=7)).month != nominal.month
                out.append(Expiry(exchange=exch, index=idx, monthly=monthly))
    return out


def next_expiry(index: str, today: date, now_minute: int = 0) -> tuple[date, bool] | None:
    """Next expiry date for ``index`` ("NIFTY" or "SENSEX") on or after ``today``.

    Returns ``(date, monthly)``. Today's expiry is skipped once the cash close (15:30) has passed.
    """
    late = now_minute >= NSE_CASH[1]
    for i in range(21):
        d = today + timedelta(days=i)
        if not is_nse_trading_day(d):
            continue
        hit = next((e for e in expiries_for(d) if e.index == index), None)
        if hit and not (i == 0 and late):
            return d, hit.monthly
    return None  # pragma: no cover - there is always an expiry within 3 weeks


def is_contract_expiry_day(symbol: str, segment: Segment, d: date) -> bool:
    """Whether the near contract of ``symbol`` in ``segment`` expires on ``d``.

    Weekly expiries exist only for Nifty (NSE) and Sensex (BSE) options; every other index contract,
    and all index futures, expire monthly on the last Tuesday (NSE) or Thursday (BSE).
    """
    s = symbol.upper()
    if segment not in (Segment.FUTURES, Segment.OPTIONS):
        return False
    if s in NSE_INDEX_SYMBOLS:
        exch, weekly_idx = "NSE", "NIFTY"
    elif s in BSE_INDEX_SYMBOLS:
        exch, weekly_idx = "BSE", "SENSEX"
    else:
        return False
    for e in expiries_for(d):
        if e.exchange != exch:
            continue
        if e.monthly or (segment is Segment.OPTIONS and s == weekly_idx):
            return True
    return False


@dataclass(frozen=True)
class DaySessions:
    day: date
    weekend: bool
    holiday: str | None
    known_year: bool
    nse_pre_open: tuple[int, int] | None = None
    nse_cash: tuple[int, int] | None = None
    fno: tuple[int, int] | None = None
    #: Index contracts that expire today and stop at 15:30 (from 7 Dec 2026 only).
    fno_expiring_indices: tuple[str, ...] = field(default_factory=tuple)
    mcx: tuple[int, int] | None = None
    mcx_evening_only: bool = False


def sessions(d: date) -> DaySessions:
    data = load_holidays()
    wk = is_weekend(d)
    hol = data.nse.get(d)
    kwargs: dict = {}
    if not wk and not hol:
        new = d >= FNO_TIMING_CHANGE
        kwargs["nse_pre_open"] = NSE_PRE_OPEN
        kwargs["nse_cash"] = NSE_CASH
        kwargs["fno"] = (FNO_OPEN, FNO_CLOSE_NEW if new else FNO_CLOSE_OLD)
        kwargs["fno_expiring_indices"] = tuple(e.index for e in expiries_for(d)) if new else ()
    if not wk and d not in data.mcx_full:
        kwargs["mcx"] = (MCX_EVENING_OPEN if hol else MCX_OPEN, mcx_close(d))
        kwargs["mcx_evening_only"] = bool(hol)
    return DaySessions(day=d, weekend=wk, holiday=hol, known_year=d.year in data.known_years, **kwargs)


def market_close(d: date, segment: Segment, symbol: str) -> int | None:
    """Close (minutes after midnight IST) of the session ``symbol`` trades in on ``d``; None if closed.

    Applies the 15:30 stop for index contracts expiring on ``d`` from 7 Dec 2026.
    """
    s = sessions(d)
    if segment in (Segment.COMMODITY_FUTURES, Segment.COMMODITY_OPTIONS):
        return s.mcx[1] if s.mcx else None
    if segment in (Segment.EQUITY_INTRADAY, Segment.EQUITY_DELIVERY):
        return s.nse_cash[1] if s.nse_cash else None
    if s.fno is None:
        return None
    if d >= FNO_TIMING_CHANGE and is_contract_expiry_day(symbol, segment, d):
        return EXPIRING_INDEX_CLOSE
    return s.fno[1]
