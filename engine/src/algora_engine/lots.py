"""Date-effective lot sizes (units per lot, i.e. the P&L multiplier per point).

NSE index lots (verified): NSE circular NSE/FAOP/70616 (3 Oct 2025, Circular Ref 176/2025)
https://nsearchives.nseindia.com/content/circulars/FAOP70616.pdf revised NIFTY 75 -> 65, BANKNIFTY 35 -> 30,
FINNIFTY 65 -> 60, MIDCPNIFTY 140 -> 120; NIFTYNXT50 unchanged at 25. Weekly and monthly contracts kept the old lot
up to the 30 Dec 2025 expiry, so the new lot applies to contracts traded for the January 2026 series onward,
i.e. from trade date 31 Dec 2025 for the near contract. The circular confirms the old lot was in force when it was
issued; the date the old lot itself began is not encoded (``start=None``).

Other symbols are not verified here and are marked so. Stock F&O lots change frequently and must come from the
exchange contract master: pass ``Instrument.lot_size`` for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .instruments import DERIVATIVE_SEGMENTS, Segment

NEW_INDEX_LOTS_FROM = date(2025, 12, 31)
_FAOP70616 = "NSE/FAOP/70616 (3 Oct 2025)"


@dataclass(frozen=True)
class LotPeriod:
    start: date | None
    end: date | None
    size: int
    source: str
    verified: bool

    def covers(self, d: date) -> bool:
        return (self.start is None or self.start <= d) and (self.end is None or d <= self.end)


_OLD_END = date(2025, 12, 30)

LOT_TABLE: dict[str, list[LotPeriod]] = {
    "NIFTY": [
        LotPeriod(None, _OLD_END, 75, _FAOP70616 + " (present lot)", True),
        LotPeriod(NEW_INDEX_LOTS_FROM, None, 65, _FAOP70616, True),
    ],
    "BANKNIFTY": [
        LotPeriod(None, _OLD_END, 35, _FAOP70616 + " (present lot)", True),
        LotPeriod(NEW_INDEX_LOTS_FROM, None, 30, _FAOP70616, True),
    ],
    "FINNIFTY": [
        LotPeriod(None, _OLD_END, 65, _FAOP70616 + " (present lot)", True),
        LotPeriod(NEW_INDEX_LOTS_FROM, None, 60, _FAOP70616, True),
    ],
    "MIDCPNIFTY": [
        LotPeriod(None, _OLD_END, 140, _FAOP70616 + " (present lot)", True),
        LotPeriod(NEW_INDEX_LOTS_FROM, None, 120, _FAOP70616, True),
    ],
    "NIFTYNXT50": [LotPeriod(None, None, 25, _FAOP70616 + " (unchanged)", True)],
    # BSE Notice 20241021-13 as cited by secondary sources; BSE notice not read directly.
    "SENSEX": [LotPeriod(None, None, 20, "BSE Notice 20241021-13 (secondary source)", False)],
    # MCX: units per lot in the price quotation unit (e.g. GOLDM is quoted per 10 g, lot 100 g -> 10).
    # From MCX contract specifications as commonly published; NOT verified against current MCX specs.
    "CRUDEOIL": [LotPeriod(None, None, 100, "MCX contract spec (unverified)", False)],
    "CRUDEOILM": [LotPeriod(None, None, 10, "MCX contract spec (unverified)", False)],
    "NATURALGAS": [LotPeriod(None, None, 1250, "MCX contract spec (unverified)", False)],
    "NATGASMINI": [LotPeriod(None, None, 250, "MCX contract spec (unverified)", False)],
    "GOLD": [LotPeriod(None, None, 100, "MCX contract spec (unverified)", False)],
    "GOLDM": [LotPeriod(None, None, 10, "MCX contract spec (unverified)", False)],
    "SILVER": [LotPeriod(None, None, 30, "MCX contract spec (unverified)", False)],
    "SILVERM": [LotPeriod(None, None, 5, "MCX contract spec (unverified)", False)],
}


class LotSizeUnknown(LookupError):
    pass


@dataclass(frozen=True)
class LotInfo:
    size: int
    source: str
    verified: bool


def lot_size(symbol: str, on: date, segment: Segment, override: int | None = None) -> LotInfo:
    """Units per lot for ``symbol`` traded on ``on``. Cash-equity "lots" are single shares."""
    if override is not None:
        if override < 1:
            raise ValueError("lot size override must be >= 1")
        return LotInfo(override, "strategy override", True)
    if segment not in DERIVATIVE_SEGMENTS:
        return LotInfo(1, "cash equity: 1 share per unit", True)
    periods = LOT_TABLE.get(symbol.upper())
    if not periods:
        raise LotSizeUnknown(f"no lot size on file for {symbol}; set instrument.lot_size")
    for p in periods:
        if p.covers(on):
            return LotInfo(p.size, p.source, p.verified)
    raise LotSizeUnknown(f"no lot size for {symbol} on {on}")  # pragma: no cover - tables are open-ended
