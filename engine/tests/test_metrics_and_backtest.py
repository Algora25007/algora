from datetime import date, datetime

import pytest
from conftest import make_bars

from algora_engine.backtest import BacktestConfig, BacktestError, RiskConfig, resample, run_backtest
from algora_engine.charges import ChargesBreakdown
from algora_engine.dsl import Condition, ConstOperand, EntryRules, Instrument, PriceOperand, StrategySpec
from algora_engine.execution import SimulatedFills
from algora_engine.instruments import Exchange, Segment
from algora_engine.lots import LotSizeUnknown, lot_size
from algora_engine.metrics import equity_curve, max_drawdown, per_day, summarize
from algora_engine.rules import RuleSignals, Side
from algora_engine.templates import first_candle_cross
from algora_engine.trader import ClosedTrade, Position

NO_COSTS = BacktestConfig(apply_charges=False)


def trade(day, net, gross=None, charges=None, side=Side.LONG, reason="Target"):
    ts = datetime.fromisoformat(f"{day}T10:00")
    gross = net if gross is None else gross
    return ClosedTrade(date.fromisoformat(day), side, 1, ts, 100, ts, 100, reason, 0, gross, charges, net)


def test_summary_hand_computed():
    # nets: +100, -50, +30, -80, 0 ; cumulative 100, 50, 80, 0, 0 ; peak 100 -> max drawdown 100
    ts = [
        trade("2026-06-01", 100),
        trade("2026-06-01", -50, side=Side.SHORT, reason="Stop"),
        trade("2026-06-02", 30),
        trade("2026-06-02", -80, reason="Stop"),
        trade("2026-06-03", 0, reason="Square-off"),
    ]
    s = summarize(ts)
    assert s["trades"] == 5 and s["wins"] == 2 and s["losses"] == 3
    assert s["win_rate_pct"] == 40.0
    assert s["net_pnl"] == 0 and s["gross_profit"] == 130 and s["gross_loss"] == 130
    assert s["profit_factor"] == 1.0
    assert s["max_drawdown"] == 100
    assert s["avg_win"] == 65 and s["avg_loss"] == pytest.approx(-43.33)
    assert s["expectancy"] == 0 and s["best_trade"] == 100 and s["worst_trade"] == -80
    assert s["max_consecutive_losses"] == 2
    assert s["long_trades"] == 4 and s["short_trades"] == 1
    assert s["exit_reasons"] == {"Square-off": 1, "Stop": 2, "Target": 2}
    # per day: +50, -50, 0
    assert s["days_traded"] == 3 and s["winning_days"] == 1 and s["losing_days"] == 1
    assert s["best_day"] == 50 and s["worst_day"] == -50 and s["avg_day"] == 0


def test_summary_empty_and_no_losses():
    s = summarize([])
    assert s["trades"] == 0 and s["win_rate_pct"] is None and s["profit_factor"] is None and s["best_day"] is None
    s = summarize([trade("2026-06-01", 10)])
    assert s["profit_factor"] is None and s["gross_profit"] == 10  # UI shows infinity


def test_drawdown_starts_from_zero_peak():
    assert max_drawdown([-10, -30, 5]) == 30
    assert max_drawdown([]) == 0


def test_per_day_and_equity_curve_with_charges():
    ch = ChargesBreakdown(*(map(__import__("decimal").Decimal, ("1", "2", "0", "0", "0", "0"))), extrapolated=False)
    ts = [trade("2026-06-01", 7, gross=10, charges=ch), trade("2026-06-01", -13, gross=-10, charges=ch)]
    days = per_day(ts)
    assert days == [{"day": "2026-06-01", "trades": 2, "wins": 1, "gross_pnl": 0.0, "charges": 6.0, "net_pnl": -6.0}]
    eq = equity_curve(ts)
    assert [p["equity"] for p in eq] == [0.0, 7.0, -6.0] and eq[0]["ts"] is None
    assert summarize(ts)["total_charges"] == 6.0


def test_resample_aligns_to_session_start():
    bars = make_bars("2026-06-03", "09:15", 5, [(1, 2, 0.5, 1.5), (1.5, 3, 1, 2), (2, 2.5, 1.5, 2.2), (2.2, 4, 2, 3)])
    out = resample(bars, 15, 555)
    assert [(b.ts.strftime("%H:%M"), b.open, b.high, b.low, b.close) for b in out] == [
        ("09:15", 1, 3, 0.5, 2.2),
        ("09:30", 2.2, 4, 2, 3),
    ]
    assert out[0].volume is None


def test_resample_sums_volume():
    from algora_engine.candles import Bar

    t = datetime(2026, 6, 3, 9, 15)
    bars = [Bar(t, 1, 1, 1, 1, 5), Bar(t.replace(minute=20), 1, 1, 1, 1, 7)]
    assert resample(bars, 15, 555)[0].volume == 12


def test_backtest_resamples_and_warns(unit_future):
    # 5m candles combined into the 15m base fixture of test_first_candle_cross.
    rows5 = []
    for o, h, lo, c in [
        (100, 102, 99, 101),
        (101, 102.5, 100.5, 102),
        (102, 104, 101.5, 103.5),
        (104, 105, 103, 104.5),
        (104.5, 106, 104, 105.5),
        (106, 107, 105, 106.5),
    ]:
        rows5 += [(o, h, lo, o), (o, o, o, o), (o, o, o, c)]
    bars = make_bars("2026-06-03", "09:15", 5, rows5)
    s = first_candle_cross(name="x", instrument=unit_future, square_off="10:30")
    r = run_backtest(s, bars, NO_COSTS)
    assert r.trades[0].entry_price == 104 and r.trades[0].exit_price == 106
    assert any("Combined 5m" in w for w in r.warnings)
    assert r.meta["source_timeframe_minutes"] == 5


def test_backtest_input_errors(unit_future):
    s = first_candle_cross(name="x", instrument=unit_future)
    with pytest.raises(BacktestError, match="no candles"):
        run_backtest(s, [])
    daily = make_bars("2026-06-01", "00:00", 24 * 60, [(1, 2, 0.5, 1)] * 3)
    with pytest.raises(BacktestError, match="daily"):
        run_backtest(s, daily)
    bars30 = make_bars("2026-06-03", "09:15", 30, [(1, 2, 0.5, 1)] * 3)
    with pytest.raises(BacktestError, match="upload smaller"):
        run_backtest(s, bars30)
    bars10 = make_bars("2026-06-03", "09:15", 10, [(1, 2, 0.5, 1)] * 3)
    with pytest.raises(BacktestError, match="cannot be combined"):
        run_backtest(s, bars10)


def test_backtest_warnings_for_holidays_unknown_years_and_extrapolated_charges():
    inst = Instrument(symbol="NIFTY", exchange=Exchange.NFO, segment=Segment.FUTURES)
    s = first_candle_cross(name="x", instrument=inst)
    rows = [(1, 2, 0.5, 1)] * 3
    bars = make_bars("2026-11-24", "09:15", 15, rows) + make_bars("2027-01-05", "09:15", 15, rows)
    r = run_backtest(s, bars)
    assert any("holidays" in w for w in r.warnings)
    assert any("2027" in w for w in r.warnings)
    r = run_backtest(s, make_bars("2024-06-03", "09:15", 15, rows))
    assert any("before 1 Oct 2024" in w for w in r.warnings)


def test_backtest_commodity_holiday_and_unverified_warnings():
    inst = Instrument(symbol="CRUDEOIL", exchange=Exchange.MCX, segment=Segment.COMMODITY_OPTIONS)
    s = first_candle_cross(name="x", instrument=inst, start="09:00", square_off="23:00")
    r = run_backtest(s, make_bars("2026-12-25", "09:00", 15, [(1, 2, 0.5, 1)] * 3))
    assert any("holidays" in w for w in r.warnings)
    assert any("Lot size 100" in w for w in r.warnings)
    assert any("not verified" in w and "commodity_options" in w for w in r.warnings)


def test_expiry_day_square_off_capped_at_1530():
    # NIFTY weekly options expire Tue 8 Dec 2026: trading stops 15:30, so a 15:40 square-off becomes 15:30.
    inst = Instrument(symbol="NIFTY", exchange=Exchange.NFO, segment=Segment.OPTIONS)
    s = first_candle_cross(name="x", instrument=inst, square_off="15:40")
    rows = [(100, 102, 99, 101), (101, 104, 100.5, 103)] + [(104, 104, 104, 104)] * 24
    bars = make_bars("2026-12-08", "09:15", 15, rows)  # 09:15 .. 15:15 plus 15:30
    r = run_backtest(s, bars, NO_COSTS)
    t = r.trades[0]
    assert t.exit_reason == "Square-off" and t.exit_ts.strftime("%H:%M") == "15:30"
    assert any("Square-off moved earlier" in w for w in r.warnings)
    r = run_backtest(s, bars, BacktestConfig(apply_charges=False, use_exchange_calendar=False))
    assert r.trades[0].exit_reason == "Session end"


def test_risk_limits(unit_future):
    # Two losing round trips a day; limits stop the second.
    rows = [
        (100, 102, 99, 101),
        (101, 102, 100.5, 101.5),
        (101.5, 103, 101, 102.5),
        (104, 104.2, 101.5, 101.8),
        (101.8, 103.5, 101.5, 103),
        (103.2, 104, 101, 101.5),
        (101.5, 102, 101, 101.2),
    ]
    s = first_candle_cross(name="x", instrument=unit_future, stop_pct=1, max_entries_per_day=5, square_off="11:00")
    bars = make_bars("2026-06-03", "09:15", 15, rows)
    assert len(run_backtest(s, bars, NO_COSTS).trades) == 2
    cfg = BacktestConfig(apply_charges=False, risk=RiskConfig(daily_loss_limit=1))
    assert len(run_backtest(s, bars, cfg).trades) == 1
    cfg = BacktestConfig(apply_charges=False, risk=RiskConfig(max_trades_per_day=1))
    assert len(run_backtest(s, bars, cfg).trades) == 1
    cfg = BacktestConfig(apply_charges=False, risk=RiskConfig(stop_new_entries_after="10:00"))
    assert run_backtest(s, bars, cfg).trades == []
    with pytest.raises(ValueError):
        RiskConfig(stop_new_entries_after="25:00")


def test_result_as_dict(unit_future):
    s = first_candle_cross(name="x", instrument=unit_future, square_off="10:30")
    bars = make_bars("2026-06-03", "09:15", 15, [(100, 102, 99, 101), (101, 103, 100, 102.5), (103, 104, 102, 103)])
    d = run_backtest(s, bars).as_dict()
    assert set(d) == {"summary", "equity_curve", "per_day", "trades", "warnings", "meta"}
    assert d["trades"][0]["side"] == "Long" and d["trades"][0]["charges"]["total"] > 0


def test_lot_sizes():
    assert lot_size("NIFTY", date(2025, 12, 30), Segment.FUTURES).size == 75
    assert lot_size("NIFTY", date(2025, 12, 31), Segment.OPTIONS).size == 65
    assert lot_size("banknifty", date(2026, 6, 1), Segment.FUTURES).size == 30
    assert lot_size("NIFTYNXT50", date(2026, 6, 1), Segment.FUTURES).size == 25
    assert not lot_size("SENSEX", date(2026, 6, 1), Segment.OPTIONS).verified
    assert lot_size("INFY", date(2026, 6, 1), Segment.EQUITY_INTRADAY).size == 1
    assert lot_size("TCS", date(2026, 6, 1), Segment.FUTURES, override=175).size == 175
    with pytest.raises(LotSizeUnknown):
        lot_size("TCS", date(2026, 6, 1), Segment.FUTURES)
    with pytest.raises(ValueError):
        lot_size("TCS", date(2026, 6, 1), Segment.FUTURES, override=0)


def test_simulated_fills():
    f = SimulatedFills(slippage_points=0.02, tick_size=0.05)
    assert f.market(100, Side.LONG) == 100.05  # 100.02 rounded up to tick
    assert f.market(100, Side.SHORT) == 99.95
    assert f.market(0.05, Side.SHORT) == 0.05  # never below one tick
    assert SimulatedFills(slippage_points=0.05).market(100, Side.LONG) == 100.05  # aligned price not pushed
    assert f.limit(101.37) == 101.37
    with pytest.raises(ValueError):
        SimulatedFills(slippage_pct=-1)
    with pytest.raises(ValueError):
        SimulatedFills(tick_size=0)


def test_position_stop_levels():
    p = Position(Side.LONG, 1, datetime(2026, 6, 3), 100, None, None, 2, 110)
    assert p.stop_level() == (pytest.approx(107.8), True)
    p = Position(Side.LONG, 1, datetime(2026, 6, 3), 100, 95, None, None, 100)
    assert p.stop_level() == (95, False) and p.trail_level() is None
    p = Position(Side.SHORT, 1, datetime(2026, 6, 3), 100, 99, None, 2, 98)
    assert p.stop_level() == (99, False)  # fixed 99 is tighter than trail 99.96
    p = Position(Side.SHORT, 1, datetime(2026, 6, 3), 100, 105, None, 2, 98)
    assert p.stop_level() == (pytest.approx(99.96), True)


def _spec(conds_long, conds_short=()):
    inst = Instrument(symbol="NIFTY", exchange=Exchange.NFO, segment=Segment.FUTURES, lot_size=1)
    return StrategySpec(
        name="r", instrument=inst, timeframe="15m", entry=EntryRules(long=list(conds_long), short=list(conds_short))
    )


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        ("gt", 101, Side.LONG),
        ("gte", 102, Side.LONG),
        ("lt", 103, Side.LONG),
        ("lte", 102, Side.LONG),
        ("gt", 102, None),
        ("lt", 102, None),
    ],
)
def test_comparison_operators(op, value, expected):
    sig = RuleSignals(_spec([Condition(op=op, left=PriceOperand(field="close"), right=ConstOperand(value=value))]))
    b1, b2 = make_bars("2026-06-03", "09:15", 15, [(100, 101, 99, 100), (100, 103, 99, 102)])
    assert sig.on_bar(b1) is None  # first candle never signals
    assert sig.on_bar(b2) is expected
    assert sig.levels["first_candle_high"] == 101


def test_conflicting_long_and_short_signals_cancel():
    c = Condition(op="gt", left=PriceOperand(field="close"), right=ConstOperand(value=0))
    sig = RuleSignals(_spec([c], [c]))
    b1, b2 = make_bars("2026-06-03", "09:15", 15, [(100, 101, 99, 100), (100, 103, 99, 102)])
    sig.on_bar(b1)
    assert sig.on_bar(b2) is None
    assert RuleSignals(_spec([c])).levels is None
