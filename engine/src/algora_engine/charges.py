"""Indian trading charges: brokerage (configurable) plus statutory and exchange levies, date-effective.

Every rate is a percentage of turnover (premium turnover for options) and carries its source and the date it
took effect. Rates were checked on 26 Nov 2026 against the sources cited below. ``verified=True`` means the rate
was read from the exchange/regulator document itself (or, where noted, from a broker's published schedule that
matches it); ``verified=False`` marks anything taken from secondary sources only.

Sources
-------
[STT-2026]  NSE circular NSE/FATAX/73524 (31 Mar 2026), Finance Act 2026: from 1 Apr 2026 STT on sale of options
            0.15% of premium (was 0.10%), on exercised options 0.15% of intrinsic value (was 0.125%), on sale of
            futures 0.05% (was 0.02%). Equity rates unchanged: delivery 0.1% buy and sell, intraday 0.025% sell.
            https://nsearchives.nseindia.com/content/circulars/FATAX73524.pdf
[STT-2024]  NSE/FATAX/63809 (9 Sep 2024), Finance (No. 2) Act 2024: options 0.1% of premium, futures 0.02%, from
            1 Oct 2024 (rates "upto March 31, 2026" in [STT-2026] confirm them).
[NSE-TXN]   NSE circular NSE/FA/73061 (27 Feb 2026): total outflow per crore, each side, unchanged since
            1 Oct 2024 (NSE/FA/64232) and only re-split between transaction charge and IPFT from 1 Mar 2026:
            cash ₹307 (0.00307%), equity futures ₹183 (0.00183%), equity options ₹3,553 of premium (0.03553%).
            These totals include the NSE IPFT contribution. https://nsearchives.nseindia.com/content/circulars/FA73061.pdf
[BSE-TXN]   Zerodha charges page (read 26 Nov 2026): BSE cash 0.00375% (group A/B, ₹375/crore since 1 Dec 2022),
            BSE equity futures 0, BSE Sensex/Bankex options 0.0325% of premium. https://zerodha.com/charges/
            Not read from a BSE notice directly.
[MCX-TXN]   MCX circular MCX/F&A/631/2024 (24 Sep 2024), from 1 Oct 2024: futures ₹2.10 per lakh (0.0021%), options
            ₹41.80 per lakh of premium (0.0418%). Matches https://zerodha.com/charges/ (26 Nov 2026).
[CTT]       MCXCCL Commodities Transaction Tax table (Finance Act 2013 s.117 as amended 2020): sale of non-agri
            commodity futures 0.01% (seller), sale of options on commodity futures 0.05% of premium (seller).
            Agri commodities are exempt. https://www.mcxccl.com/clearing-settlement/commodities-transaction-tax
[SEBI]      SEBI turnover fee ₹10 per crore (0.0001%) on all non-debt trades, SEBI (Payment of Fees) (Amendment)
            Regulations 2019, as listed by NSE https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies
            Agri commodity futures ₹1 per crore per https://zerodha.com/charges/.
[STAMP]     Indian Stamp Act amendment (Finance Act 2019), in force 1 Jul 2020, buyer pays: delivery 0.015%,
            non-delivery 0.003%, equity futures 0.002%, equity options 0.003% of premium, commodity futures 0.002%.
            https://www.nseindia.com/static/invest/first-time-investor-stamp-duty-charges-taxes
            Commodity options 0.003%: Zerodha charges page (not on the NSE page).
[GST]       18% on (brokerage + exchange transaction charges + SEBI fee), per https://zerodha.com/charges/
            (26 Nov 2026). Not stamp duty or STT.
[BROKERAGE] Broker-specific. The default plan mirrors Zerodha's published schedule (26 Nov 2026): delivery ₹0;
            intraday, futures and commodity futures 0.03% or ₹20 per executed order, whichever is lower; options
            and commodity options flat ₹20 per executed order. Configure per user/broker.

Known simplifications (documented, not hidden):
* STT on exercised options (0.15% of intrinsic value) is not modelled: v1 strategies square off intraday.
* Contract notes round STT to the rupee per scrip per day; this module rounds each component to the paisa.
* Dates before the earliest table (1 Oct 2024) use that table and are flagged ``extrapolated``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .instruments import Exchange, Segment

D = Decimal
HUNDRED = D(100)
PAISA = D("0.01")
TABLE_START = date(2024, 10, 1)
SWITCH_STT_2026 = date(2026, 4, 1)


@dataclass(frozen=True)
class Period:
    start: date
    end: date | None  # inclusive; None = still in force
    pct: Decimal
    source: str
    verified: bool = True

    def covers(self, d: date) -> bool:
        return self.start <= d and (self.end is None or d <= self.end)


def _p(start: date, end: date | None, pct: str, source: str, verified: bool = True) -> Period:
    return Period(start, end, D(pct), source, verified)


_PRE = date(2026, 3, 31)
_ZERO = "0"

# Component keys: stt_buy, stt_sell, txn (each side), sebi (each side), stamp_buy, gst (on brokerage+txn+sebi).
_SEBI = [_p(TABLE_START, None, "0.0001", "[SEBI]")]
_GST = [_p(TABLE_START, None, "18", "[GST]")]

_TABLE: dict[tuple[Segment, Exchange], dict[str, list[Period]]] = {}


def _equity(exch: Exchange, txn: Period) -> None:
    _TABLE[(Segment.EQUITY_INTRADAY, exch)] = {
        "stt_buy": [_p(TABLE_START, None, _ZERO, "[STT-2026]")],
        "stt_sell": [_p(TABLE_START, None, "0.025", "[STT-2026]")],
        "txn": [txn],
        "sebi": _SEBI,
        "stamp_buy": [_p(TABLE_START, None, "0.003", "[STAMP]")],
        "gst": _GST,
    }
    _TABLE[(Segment.EQUITY_DELIVERY, exch)] = {
        "stt_buy": [_p(TABLE_START, None, "0.1", "[STT-2026]")],
        "stt_sell": [_p(TABLE_START, None, "0.1", "[STT-2026]")],
        "txn": [txn],
        "sebi": _SEBI,
        "stamp_buy": [_p(TABLE_START, None, "0.015", "[STAMP]")],
        "gst": _GST,
    }


_equity(Exchange.NSE, _p(TABLE_START, None, "0.00307", "[NSE-TXN]"))
_equity(Exchange.BSE, _p(TABLE_START, None, "0.00375", "[BSE-TXN]", verified=False))

_FUT_STT = [
    _p(TABLE_START, _PRE, "0.02", "[STT-2024]"),
    _p(SWITCH_STT_2026, None, "0.05", "[STT-2026]"),
]
_OPT_STT = [
    _p(TABLE_START, _PRE, "0.1", "[STT-2024]"),
    _p(SWITCH_STT_2026, None, "0.15", "[STT-2026]"),
]
for _exch, _fut_txn, _opt_txn in (
    (Exchange.NFO, _p(TABLE_START, None, "0.00183", "[NSE-TXN]"), _p(TABLE_START, None, "0.03553", "[NSE-TXN]")),
    (
        Exchange.BFO,
        _p(TABLE_START, None, "0", "[BSE-TXN]", verified=False),
        _p(TABLE_START, None, "0.0325", "[BSE-TXN]", verified=False),
    ),
):
    _TABLE[(Segment.FUTURES, _exch)] = {
        "stt_buy": [_p(TABLE_START, None, _ZERO, "[STT-2026]")],
        "stt_sell": _FUT_STT,
        "txn": [_fut_txn],
        "sebi": _SEBI,
        "stamp_buy": [_p(TABLE_START, None, "0.002", "[STAMP]")],
        "gst": _GST,
    }
    _TABLE[(Segment.OPTIONS, _exch)] = {
        "stt_buy": [_p(TABLE_START, None, _ZERO, "[STT-2026]")],
        "stt_sell": _OPT_STT,
        "txn": [_opt_txn],
        "sebi": _SEBI,
        "stamp_buy": [_p(TABLE_START, None, "0.003", "[STAMP]")],
        "gst": _GST,
    }

_TABLE[(Segment.COMMODITY_FUTURES, Exchange.MCX)] = {
    "stt_buy": [_p(TABLE_START, None, _ZERO, "[CTT]")],
    "stt_sell": [_p(TABLE_START, None, "0.01", "[CTT]")],
    "txn": [_p(TABLE_START, None, "0.0021", "[MCX-TXN]")],
    "sebi": _SEBI,
    "stamp_buy": [_p(TABLE_START, None, "0.002", "[STAMP]")],
    "gst": _GST,
}
_TABLE[(Segment.COMMODITY_OPTIONS, Exchange.MCX)] = {
    "stt_buy": [_p(TABLE_START, None, _ZERO, "[CTT]")],
    "stt_sell": [_p(TABLE_START, None, "0.05", "[CTT]")],
    "txn": [_p(TABLE_START, None, "0.0418", "[MCX-TXN]")],
    "sebi": _SEBI,
    "stamp_buy": [_p(TABLE_START, None, "0.003", "[STAMP]", verified=False)],
    "gst": _GST,
}
#: Agricultural commodity futures: no CTT, SEBI fee ₹1/crore ([SEBI], broker-published).
_AGRI_OVERRIDES = {
    "stt_sell": [_p(TABLE_START, None, _ZERO, "[CTT]")],
    "sebi": [_p(TABLE_START, None, "0.00001", "[SEBI]", verified=False)],
}


class RatesUnavailable(LookupError):
    pass


@dataclass(frozen=True)
class ResolvedRates:
    segment: Segment
    exchange: Exchange
    on: date
    stt_buy: Decimal
    stt_sell: Decimal
    txn: Decimal
    sebi: Decimal
    stamp_buy: Decimal
    gst: Decimal
    extrapolated: bool
    sources: tuple[str, ...]
    all_verified: bool


def rates_on(segment: Segment, exchange: Exchange, on: date, *, agri: bool = False) -> ResolvedRates:
    """The statutory/exchange rates in force for ``segment`` on ``exchange`` on trade date ``on``."""
    table = _TABLE.get((segment, exchange))
    if table is None:
        raise RatesUnavailable(f"no charges table for {segment.value} on {exchange.value}")
    if agri:
        if segment is not Segment.COMMODITY_FUTURES:
            raise RatesUnavailable("agri rates are encoded for commodity futures only")
        table = {**table, **_AGRI_OVERRIDES}
    extrapolated = on < TABLE_START
    lookup = TABLE_START if extrapolated else on
    chosen: dict[str, Period] = {}
    for key, periods in table.items():
        match = next((p for p in periods if p.covers(lookup)), None)
        if match is None:  # pragma: no cover - tables are contiguous from TABLE_START
            raise RatesUnavailable(f"no {key} rate for {segment.value}/{exchange.value} on {on}")
        chosen[key] = match
    return ResolvedRates(
        segment=segment,
        exchange=exchange,
        on=on,
        stt_buy=chosen["stt_buy"].pct,
        stt_sell=chosen["stt_sell"].pct,
        txn=chosen["txn"].pct,
        sebi=chosen["sebi"].pct,
        stamp_buy=chosen["stamp_buy"].pct,
        gst=chosen["gst"].pct,
        extrapolated=extrapolated,
        sources=tuple(sorted({p.source for p in chosen.values()})),
        all_verified=all(p.verified for p in chosen.values()),
    )


def rate_table() -> list[dict]:
    """Every encoded rate period, for documentation and the API."""
    rows = []
    for (seg, exch), comps in sorted(_TABLE.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value)):
        for comp, periods in comps.items():
            for p in periods:
                rows.append(
                    {
                        "segment": seg.value,
                        "exchange": exch.value,
                        "component": comp,
                        "pct": str(p.pct),
                        "from": p.start.isoformat(),
                        "to": p.end.isoformat() if p.end else None,
                        "source": p.source,
                        "verified": p.verified,
                    }
                )
    return rows


Money = Annotated[Decimal, Field(ge=0, le=D(1_000_000))]


class BrokerageRule(BaseModel):
    """Per executed order: ``flat`` if set, otherwise ``pct`` of order value capped at ``cap``."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    pct: Annotated[Decimal, Field(ge=0, le=5)] = D(0)
    cap: Money | None = None
    flat: Money | None = None

    def for_order(self, value: Decimal) -> Decimal:
        if self.flat is not None:
            return self.flat
        fee = value * self.pct / HUNDRED
        return min(fee, self.cap) if self.cap is not None else fee


class BrokeragePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Annotated[str, Field(min_length=1, max_length=60)] = "custom"
    rules: dict[Segment, BrokerageRule]

    def rule(self, segment: Segment) -> BrokerageRule:
        return self.rules.get(segment, BrokerageRule())


_PCT_CAP_20 = BrokerageRule(pct=D("0.03"), cap=D(20))
_FLAT_20 = BrokerageRule(flat=D(20))

#: Mirrors Zerodha's published schedule as read on 26 Nov 2026 ([BROKERAGE]).
DISCOUNT_BROKER_PLAN = BrokeragePlan(
    name="discount-20",
    rules={
        Segment.EQUITY_DELIVERY: BrokerageRule(),
        Segment.EQUITY_INTRADAY: _PCT_CAP_20,
        Segment.FUTURES: _PCT_CAP_20,
        Segment.OPTIONS: _FLAT_20,
        Segment.COMMODITY_FUTURES: _PCT_CAP_20,
        Segment.COMMODITY_OPTIONS: _FLAT_20,
    },
)
ZERO_BROKERAGE_PLAN = BrokeragePlan(name="zero", rules={})


@dataclass(frozen=True)
class ChargesBreakdown:
    brokerage: Decimal
    stt: Decimal
    exchange_txn: Decimal
    sebi: Decimal
    stamp: Decimal
    gst: Decimal
    extrapolated: bool

    @property
    def total(self) -> Decimal:
        return self.brokerage + self.stt + self.exchange_txn + self.sebi + self.stamp + self.gst

    def as_dict(self) -> dict[str, float]:
        return {
            "brokerage": float(self.brokerage),
            "stt": float(self.stt),
            "exchange_txn": float(self.exchange_txn),
            "sebi": float(self.sebi),
            "stamp": float(self.stamp),
            "gst": float(self.gst),
            "total": float(self.total),
        }


def _r(v: Decimal) -> Decimal:
    return v.quantize(PAISA, rounding=ROUND_HALF_UP)


def compute_charges(
    *,
    segment: Segment,
    exchange: Exchange,
    trade_date: date,
    buy_value: Decimal | float,
    sell_value: Decimal | float,
    plan: BrokeragePlan = DISCOUNT_BROKER_PLAN,
    buy_orders: int = 1,
    sell_orders: int = 1,
    agri: bool = False,
) -> ChargesBreakdown:
    """Charges for a set of buys and sells of one instrument on one day.

    ``buy_value``/``sell_value`` are turnover in rupees (premium turnover for options). Brokerage is charged per
    executed order; each side's value is split evenly across its orders for percentage brokerage.
    """
    bv, sv = D(str(buy_value)), D(str(sell_value))
    if bv < 0 or sv < 0:
        raise ValueError("turnover must be non-negative")
    if buy_orders < 0 or sell_orders < 0:
        raise ValueError("order counts must be non-negative")
    r = rates_on(segment, exchange, trade_date, agri=agri)
    rule = plan.rule(segment)
    brokerage = D(0)
    for value, n in ((bv, buy_orders), (sv, sell_orders)):
        if n:
            brokerage += rule.for_order(value / n) * n
    brokerage = _r(brokerage)
    turnover = bv + sv
    stt = _r(bv * r.stt_buy / HUNDRED + sv * r.stt_sell / HUNDRED)
    txn = _r(turnover * r.txn / HUNDRED)
    sebi = _r(turnover * r.sebi / HUNDRED)
    stamp = _r(bv * r.stamp_buy / HUNDRED)
    gst = _r((brokerage + txn + sebi) * r.gst / HUNDRED)
    return ChargesBreakdown(
        brokerage=brokerage, stt=stt, exchange_txn=txn, sebi=sebi, stamp=stamp, gst=gst, extrapolated=r.extrapolated
    )
