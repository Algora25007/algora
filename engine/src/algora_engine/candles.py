"""Candles and the CSV reader (a port of the browser's parseCSV, with stricter validation).

Timestamps are IST wall-clock, naive ``datetime`` objects, and mark the candle's *open* time (the convention of
NSE/MCX data vendors and broker historical APIs).
"""

from __future__ import annotations

import itertools
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    @property
    def day(self) -> date:
        return self.ts.date()

    @property
    def minute(self) -> int:
        return self.ts.hour * 60 + self.ts.minute


@dataclass(frozen=True)
class ParsedCandles:
    bars: list[Bar]
    timeframe_minutes: int  # 0 when the file holds daily candles (one per day)
    skipped_unreadable: int
    skipped_duplicate: int
    skipped_invalid: int

    @property
    def skipped(self) -> int:
        return self.skipped_unreadable + self.skipped_duplicate + self.skipped_invalid

    @property
    def sessions(self) -> int:
        return len({b.day for b in self.bars})


class CandleFileError(ValueError):
    pass


_EPOCH = re.compile(r"^\d{9,13}(\.\d+)?$")
_YMD = re.compile(
    r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?\s*"
    r"(am|pm|AM|PM)?\s*(Z|[+-]\d{2}:?\d{2})?$"
)
_DMY = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*(am|pm|AM|PM)?$")


def _mk(y: int, mo: int, d: int, h: int, mi: int, sec: int, ampm: str | None) -> datetime | None:
    if ampm:
        if h < 1 or h > 12:
            return None
        if ampm.lower() == "pm" and h < 12:
            h += 12
        elif ampm.lower() == "am" and h == 12:
            h = 0
    try:
        return datetime(y, mo, d, h, mi, sec)
    except ValueError:
        return None


def parse_when(s: str) -> datetime | None:
    """Parse one timestamp to IST wall-clock. Accepts epoch s/ms (UTC), Y-M-D and D-M-Y with optional time."""
    s = s.strip()
    if _EPOCH.match(s):
        whole = s.split(".")[0]
        secs = float(s) / 1000 if len(whole) > 11 else float(s)
        return datetime.fromtimestamp(secs, tz=UTC).astimezone(IST).replace(tzinfo=None)
    m = _YMD.match(s)
    if m:
        y, mo, d, h, mi, sec, ampm, tz = m.groups()
        dt = _mk(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(sec or 0), ampm)
        if dt is None:
            return None
        if tz:
            if tz == "Z":
                off = timedelta(0)
            else:
                sign = 1 if tz[0] == "+" else -1
                digits = tz[1:].replace(":", "")
                off = sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
            dt = dt.replace(tzinfo=timezone(off)).astimezone(IST).replace(tzinfo=None)
        return dt
    m = _DMY.match(s)
    if m:
        d, mo, y, h, mi, sec, ampm = m.groups()
        return _mk(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(sec or 0), ampm)
    return None


def _split(line: str, sep: str) -> list[str]:
    return [c.strip().strip('"') for c in line.split(sep)]


def parse_csv(text: str, *, max_rows: int = 500_000) -> ParsedCandles:
    """Read OHLC candles. Columns (any order, case-insensitive): datetime|date_time|timestamp|date, optional time,
    open, high, low, close, optional volume. Separator: comma, semicolon or tab.
    """
    lines = [ln for ln in text.lstrip("\ufeff").splitlines() if ln.strip()]
    if len(lines) < 2:
        raise CandleFileError("The file has no candle rows.")
    if len(lines) - 1 > max_rows:
        raise CandleFileError(f"The file has more than {max_rows:,} rows.")
    head_line = lines[0]
    sep = "," if "," in head_line else ";" if ";" in head_line else "\t"
    head = [h.lower() for h in _split(head_line, sep)]

    def col(names: list[str]) -> int:
        for n in names:
            if n in head:
                return head.index(n)
        return -1

    i_dt = col(["datetime", "date_time", "timestamp", "date"])
    i_time = -1 if "datetime" in head else col(["time"])
    idx = {k: col([k]) for k in ("open", "high", "low", "close")}
    i_vol = col(["volume", "vol"])
    missing = (["datetime"] if i_dt < 0 else []) + [k for k, v in idx.items() if v < 0]
    if missing:
        raise CandleFileError("Missing columns: " + ", ".join(missing) + ". Expected datetime, open, high, low, close.")
    bars: list[Bar] = []
    seen: set[datetime] = set()
    unreadable = dup = invalid = 0
    for line in lines[1:]:
        c = _split(line, sep)
        try:
            when_s = c[i_dt] + ((" " + c[i_time]) if i_time > -1 and i_time < len(c) else "")
            ts = parse_when(when_s)
            o, h, lo, cl = (float(c[idx[k]]) for k in ("open", "high", "low", "close"))
            vol = float(c[i_vol]) if i_vol > -1 and i_vol < len(c) and c[i_vol] != "" else None
        except (IndexError, ValueError):
            unreadable += 1
            continue
        if ts is None or any(v != v or v in (float("inf"), float("-inf")) for v in (o, h, lo, cl)):
            unreadable += 1
            continue
        if min(o, h, lo, cl) <= 0 or h < max(o, cl, lo) or lo > min(o, cl, h):
            invalid += 1
            continue
        if ts in seen:
            dup += 1
            continue
        seen.add(ts)
        bars.append(Bar(ts, o, h, lo, cl, vol))
    if not bars:
        raise CandleFileError("No valid candle rows found. Check the datetime format.")
    bars.sort(key=lambda b: b.ts)
    return ParsedCandles(
        bars=bars,
        timeframe_minutes=detect_timeframe(bars),
        skipped_unreadable=unreadable,
        skipped_duplicate=dup,
        skipped_invalid=invalid,
    )


def detect_timeframe(bars: list[Bar]) -> int:
    """Most common gap in minutes between consecutive candles of the same day; 0 for daily data."""
    gaps: Counter[int] = Counter()
    for a, b in itertools.pairwise(bars):
        if a.day == b.day:
            gaps[int((b.ts - a.ts).total_seconds() // 60)] += 1
    if not gaps:
        return 0
    # Ties resolve to the smaller gap, deterministically.
    best = max(gaps.values())
    return min(g for g, n in gaps.items() if n == best)
