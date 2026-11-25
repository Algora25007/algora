"""Charges with hand-computed expectations (each component rounded to the paisa, half-up)."""

from datetime import date
from decimal import Decimal

import pytest

from algora_engine.charges import (
    DISCOUNT_BROKER_PLAN,
    ZERO_BROKERAGE_PLAN,
    BrokeragePlan,
    BrokerageRule,
    RatesUnavailable,
    compute_charges,
    rate_table,
    rates_on,
)
from algora_engine.instruments import Exchange, Segment

AFTER = date(2026, 6, 3)
BEFORE = date(2026, 3, 20)


def c(segment, exchange, buy, sell, on=AFTER, **kw):
    return compute_charges(segment=segment, exchange=exchange, trade_date=on, buy_value=buy, sell_value=sell, **kw)


def test_nse_futures_after_stt_hike():
    # brokerage min(20, 0.03% of 10 lakh = 300) x 2 = 40 ; STT 0.05% x 10,10,000 = 505
    # txn 0.00183% x 20,10,000 = 36.783 -> 36.78 ; SEBI 2.01 ; stamp 0.002% x 10 lakh = 20
    # GST 18% x (40 + 36.78 + 2.01) = 14.1822 -> 14.18 ; total 617.97
    r = c(Segment.FUTURES, Exchange.NFO, 1_000_000, 1_010_000)
    assert (r.brokerage, r.stt, r.exchange_txn, r.sebi, r.stamp, r.gst) == tuple(
        map(Decimal, ("40.00", "505.00", "36.78", "2.01", "20.00", "14.18"))
    )
    assert r.total == Decimal("617.97") and not r.extrapolated


def test_nse_futures_before_stt_hike():
    r = c(Segment.FUTURES, Exchange.NFO, 1_000_000, 1_010_000, on=BEFORE)
    assert r.stt == Decimal("202.00") and r.total == Decimal("314.97")


def test_stt_switch_date_is_1_april_2026():
    assert rates_on(Segment.OPTIONS, Exchange.NFO, date(2026, 3, 31)).stt_sell == Decimal("0.1")
    assert rates_on(Segment.OPTIONS, Exchange.NFO, date(2026, 4, 1)).stt_sell == Decimal("0.15")


def test_nse_options():
    # premium 100 -> 120, 1 lot of 65: buy 6,500 sell 7,800
    # brokerage flat 20 x 2 ; STT 0.15% x 7,800 = 11.70 ; txn 0.03553% x 14,300 = 5.08079 -> 5.08
    # SEBI 0.0143 -> 0.01 ; stamp 0.003% x 6,500 = 0.195 -> 0.20 ; GST 18% x 45.09 = 8.1162 -> 8.12 ; total 65.11
    r = c(Segment.OPTIONS, Exchange.NFO, 6500, 7800)
    assert (r.stt, r.exchange_txn, r.sebi, r.stamp, r.gst, r.total) == tuple(
        map(Decimal, ("11.70", "5.08", "0.01", "0.20", "8.12", "65.11"))
    )


def test_equity_intraday():
    # brokerage min(20, 15) + min(20, 15.30) = 30.30 ; STT 0.025% x 51,000 = 12.75 ; txn 0.00307% x 1,01,000 = 3.10
    # SEBI 0.10 ; stamp 0.003% x 50,000 = 1.50 ; GST 18% x 33.50 = 6.03 ; total 53.78
    r = c(Segment.EQUITY_INTRADAY, Exchange.NSE, 50_000, 51_000)
    assert r.brokerage == Decimal("30.30") and r.total == Decimal("53.78")


def test_equity_delivery():
    # STT 0.1% both sides = 200 ; txn 6.14 ; SEBI 0.20 ; stamp 0.015% = 15 ; GST 18% x 6.34 = 1.1412 -> 1.14
    r = c(Segment.EQUITY_DELIVERY, Exchange.NSE, 100_000, 100_000)
    assert r.brokerage == 0 and r.stt == Decimal("200.00") and r.total == Decimal("222.48")


def test_mcx_futures_and_options():
    # Futures: CTT 0.01% x 5,05,000 = 50.50 ; txn 0.0021% x 10,05,000 = 21.105 -> 21.11 ; SEBI 1.005 -> 1.01
    # stamp 10.00 ; GST 18% x 62.12 = 11.1816 -> 11.18 ; total 133.80
    r = c(Segment.COMMODITY_FUTURES, Exchange.MCX, 500_000, 505_000)
    assert r.total == Decimal("133.80") and r.stt == Decimal("50.50")
    # Options: CTT 0.05% x 12,000 = 6 ; txn 0.0418% x 22,000 = 9.196 -> 9.20 ; SEBI 0.02 ; stamp 0.30
    # GST 18% x 49.22 = 8.8596 -> 8.86 ; total 64.38
    r = c(Segment.COMMODITY_OPTIONS, Exchange.MCX, 10_000, 12_000)
    assert r.total == Decimal("64.38")


def test_mcx_agri_futures_no_ctt():
    r = c(Segment.COMMODITY_FUTURES, Exchange.MCX, 500_000, 505_000, agri=True)
    assert r.stt == 0 and r.sebi == Decimal("0.10")
    with pytest.raises(RatesUnavailable):
        c(Segment.COMMODITY_OPTIONS, Exchange.MCX, 1, 1, agri=True)


def test_bse_derivatives():
    r = c(Segment.FUTURES, Exchange.BFO, 1_000_000, 1_000_000)
    assert r.exchange_txn == 0
    assert rates_on(Segment.OPTIONS, Exchange.BFO, AFTER).txn == Decimal("0.0325")
    assert not rates_on(Segment.OPTIONS, Exchange.BFO, AFTER).all_verified
    assert rates_on(Segment.OPTIONS, Exchange.NFO, AFTER).all_verified


def test_zero_and_custom_brokerage():
    assert c(Segment.FUTURES, Exchange.NFO, 1000, 1000, plan=ZERO_BROKERAGE_PLAN).brokerage == 0
    plan = BrokeragePlan(name="pct", rules={Segment.FUTURES: BrokerageRule(pct=Decimal("0.01"))})
    # 0.01% of 2,00,000 per side, uncapped = 20 + 20
    assert c(Segment.FUTURES, Exchange.NFO, 200_000, 200_000, plan=plan).brokerage == Decimal("40.00")
    # several orders split the side's value
    r = c(Segment.OPTIONS, Exchange.NFO, 1000, 1000, plan=DISCOUNT_BROKER_PLAN, buy_orders=2, sell_orders=3)
    assert r.brokerage == Decimal("100.00")


def test_extrapolated_before_table_start():
    r = c(Segment.FUTURES, Exchange.NFO, 1000, 1000, on=date(2024, 1, 1))
    assert r.extrapolated


def test_invalid_inputs():
    with pytest.raises(ValueError):
        c(Segment.FUTURES, Exchange.NFO, -1, 0)
    with pytest.raises(ValueError):
        c(Segment.FUTURES, Exchange.NFO, 1, 1, buy_orders=-1)
    with pytest.raises(RatesUnavailable):
        c(Segment.FUTURES, Exchange.MCX, 1, 1)


def test_rate_table_lists_sources():
    rows = rate_table()
    assert all(r["source"].startswith("[") for r in rows)
    fut = [r for r in rows if r["segment"] == "futures" and r["exchange"] == "NFO" and r["component"] == "stt_sell"]
    assert [(r["pct"], r["from"], r["to"]) for r in fut] == [
        ("0.02", "2024-10-01", "2026-03-31"),
        ("0.05", "2026-04-01", None),
    ]


def test_breakdown_as_dict():
    d = c(Segment.FUTURES, Exchange.NFO, 1_000_000, 1_010_000).as_dict()
    assert d["total"] == pytest.approx(617.97) and set(d) == {
        "brokerage",
        "stt",
        "exchange_txn",
        "sebi",
        "stamp",
        "gst",
        "total",
    }
