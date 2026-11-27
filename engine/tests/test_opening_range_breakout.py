"""Opening Range Breakout on the DSL, with hand-computed fixtures.

Base day (Wed 3 Jun 2026, NIFTY future with a 1-unit lot, 5m candles from 09:15, opening range 15 min, buffer 0):
    #   time   open   high   low    close
    1   09:15  100    101    99     100.5   range
    2   09:20  100.5  101.5  100    101     range
    3   09:25  101    102    100.5  101.5   range formed at 09:30: high 102, low 99, mid 100.5, width 3
                                            range_width_pct = 3 / 101.5 x 100 = 2.956%
    4   09:30  101.5  102    101    101.8   close 101.8 is not above 102: no signal
    5   09:35  101.8  103    101.6  102.6   closes above 102 after 101.8 <= 102: LONG signal (candle closes 09:40)
    6   09:40  102.8  103.5  102.5  103.2   long fills at this open: 102.8
                                            default exits: stop = range low 99, target = 102.8 + 1 x 3 = 105.8
    7   09:45  103.2  104    103    103.8
    8   09:50  103.8  106    103.5  105.5   high 106 >= 105.8: target, exit 105.8 -> +3.00 points
    9   09:55  105.5  105.6  105    105.2
   10   10:00  105.2  105.4  105    105.1   square-off 10:00 (when a position is still open)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import make_bars
from pydantic import ValidationError

from algora_engine.backtest import BacktestConfig, run_backtest
from algora_engine.candles import Bar
from algora_engine.dsl import Instrument
from algora_engine.instruments import Exchange, Segment
from algora_engine.rules import Side
from algora_engine.templates import OpeningRangeBreakoutParams, build_template

DAY = "2026-06-03"
BASE = [
    (100, 101, 99, 100.5),
    (100.5, 101.5, 100, 101),
    (101, 102, 100.5, 101.5),
    (101.5, 102, 101, 101.8),
    (101.8, 103, 101.6, 102.6),
    (102.8, 103.5, 102.5, 103.2),
    (103.2, 104, 103, 103.8),
    (103.8, 106, 103.5, 105.5),
    (105.5, 105.6, 105, 105.2),
    (105.2, 105.4, 105, 105.1),
]
NO_COSTS = BacktestConfig(apply_charges=False)


def orb(inst, **params):
    params.setdefault("opening_range_minutes", 15)
    params.setdefault("square_off", "10:00")
    return build_template("opening_range_breakout", name="ORB", instrument=inst, params=params)


def run(inst, rows, config=NO_COSTS, day=DAY, step=5, **params):
    params.setdefault("timeframe", f"{step}m")
    return run_backtest(orb(inst, **params), make_bars(day, "09:15", step, rows), config)


def hm(ts: datetime) -> str:
    return ts.strftime("%H:%M")


def test_long_breakout_fills_next_open_and_hits_range_multiple_target(unit_future):
    r = run(unit_future, BASE)
    assert len(r.trades) == 1
    t = r.trades[0]
    assert t.side is Side.LONG
    assert (hm(t.entry_ts), t.entry_price) == ("09:40", 102.8)
    assert (hm(t.exit_ts), t.exit_reason) == ("09:50", "Target")
    assert t.exit_price == pytest.approx(105.8)
    assert t.points == pytest.approx(3.0)


def test_slippage_on_entry_and_target_measured_from_the_fill(unit_future):
    # Buy 102.8 + 0.1 slippage = 102.9 (on the 0.05 tick). Target 102.9 + 3 = 105.9 is a limit: no slippage.
    r = run(unit_future, BASE, config=BacktestConfig(apply_charges=False, slippage_points=0.1))
    t = r.trades[0]
    assert t.entry_price == pytest.approx(102.9)
    assert (t.exit_reason, t.exit_price) == ("Target", pytest.approx(105.9))


def test_short_breakout_below_range_low():
    inst = Instrument(symbol="NIFTY", exchange=Exchange.NFO, segment=Segment.FUTURES, lot_size=1)
    rows = [
        *BASE[:3],
        (100, 100.5, 99.2, 99.5),  # close 99.5 >= 99
        (99.5, 99.6, 98.4, 98.6),  # close 98.6 < 99 after 99.5 >= 99: SHORT
        (98.5, 98.8, 98.0, 98.2),  # short fills 98.5; stop = range high 102; target 98.5 - 3 = 95.5
        (98.2, 98.3, 95.0, 95.6),  # low 95 <= 95.5: target
        (95.6, 96, 95.2, 95.8),
        (95.8, 96, 95.5, 95.7),
    ]
    t = run(inst, rows).trades[0]
    assert t.side is Side.SHORT and t.entry_price == 98.5
    assert (t.exit_reason, t.exit_price, t.points) == ("Target", pytest.approx(95.5), pytest.approx(3.0))
    assert run(inst, rows, direction="long").trades == []
    assert len(run(inst, rows, direction="short").trades) == 1


def test_stop_assumed_first_when_stop_and_target_touch_same_candle(unit_future):
    # Stop at range mid 100.5; risk 102.8 - 100.5 = 2.3; 1R target 105.1. Candle 7 spans 100-105.5: both -> stop.
    rows = [*BASE[:6], (103.2, 105.5, 100.0, 101), *BASE[7:]]
    t = run(unit_future, rows, stop_type="range_mid", target_type="r_multiple", target_value=1.0).trades[0]
    assert (t.exit_reason, t.exit_price) == ("Stop", pytest.approx(100.5))
    assert t.points == pytest.approx(-2.3)
    # Same candle without the stop touch (low 101): the 1R target fills.
    rows = [*BASE[:6], (103.2, 105.5, 101.0, 105), *BASE[7:]]
    t = run(unit_future, rows, stop_type="range_mid", target_type="r_multiple", target_value=1.0).trades[0]
    assert (t.exit_reason, t.exit_price) == ("Target", pytest.approx(105.1))


def test_gap_through_range_stop_fills_at_open(unit_future):
    # Stop at the other end of the range, 99. Candle 7 opens at 98.5, already below it: exit 98.5, not 99.
    rows = [*BASE[:6], (98.5, 99.5, 98, 99), *BASE[7:]]
    t = run(unit_future, rows).trades[0]
    assert (t.exit_reason, t.exit_price, t.points) == ("Stop", 98.5, pytest.approx(-4.3))


def test_entry_fill_already_beyond_the_stop_level_exits_at_once(unit_future):
    # Signal at 09:40, but candle 6 opens at 98.8, below the 99 range-low stop. With 0.1 slippage the buy fills
    # 98.9 and the stop (already breached) sells at the same open: 98.8 - 0.1 = 98.7 -> -0.2 points.
    rows = [*BASE[:5], (98.8, 99.5, 98.5, 99.2), *BASE[6:]]
    r = run(unit_future, rows, config=BacktestConfig(apply_charges=False, slippage_points=0.1))
    t = r.trades[0]
    assert (hm(t.entry_ts), hm(t.exit_ts), t.exit_reason) == ("09:40", "09:40", "Stop")
    assert (t.entry_price, t.exit_price, t.points) == (pytest.approx(98.9), pytest.approx(98.7), pytest.approx(-0.2))


def test_buffer_in_points_and_percent(unit_future):
    # 0.7 points: level 102.7. Candle 5 (102.6) does not clear it; candle 6 (103.2) does -> fill at candle 7, 103.2.
    t = run(unit_future, BASE, buffer_type="points", buffer=0.7).trades[0]
    assert (hm(t.entry_ts), t.entry_price) == ("09:45", 103.2)
    # 0.5%: level 102 x 1.005 = 102.51 < 102.6 -> same entry as with no buffer.
    t = run(unit_future, BASE, buffer_type="pct", buffer=0.5).trades[0]
    assert (hm(t.entry_ts), t.entry_price) == ("09:40", 102.8)


@pytest.mark.parametrize(
    ("lo", "hi", "trades"),
    [(None, 2.5, 0), (3.0, None, 0), (2.5, 3.0, 1), (2.9, 3.0, 1), (None, 2.95, 0)],
)
def test_range_size_filters(unit_future, lo, hi, trades):
    # range_width_pct = 3 / 101.5 x 100 = 2.9557%
    assert len(run(unit_future, BASE, range_min_pct=lo, range_max_pct=hi).trades) == trades


def test_n_trades_per_day_needs_a_fresh_cross(unit_future):
    rows = [
        *BASE[:6],
        (102.8, 103, 100.2, 100.8),  # 09:45 low 100.2 <= stop 100.5 (range mid): stop, -2.3
        (100.8, 101.5, 100.6, 101.2),  # 09:50 close 101.2 <= 102: no signal
        (101.2, 102.6, 101, 102.4),  # 09:55 close 102.4 > 102 after 101.2: second LONG signal
        (102.5, 103, 102.3, 102.9),  # 10:00 fills 102.5
        (102.7, 103, 102.5, 102.8),  # 10:05 square-off at the open, 102.7 -> +0.2
    ]
    kw = {"stop_type": "range_mid", "target_type": "none", "target_value": None, "square_off": "10:05"}
    r = run(unit_future, rows, max_entries_per_day=2, **kw)
    assert [(t.exit_reason, round(t.points, 4)) for t in r.trades] == [("Stop", -2.3), ("Square-off", 0.2)]
    assert [t.entry_price for t in r.trades] == [102.8, 102.5]
    assert len(run(unit_future, rows, max_entries_per_day=1, **kw).trades) == 1


def test_entry_window_limits_the_signal_candle_close(unit_future):
    # The signal candle (09:35) closes at 09:40.
    assert run(unit_future, BASE, entry_end="09:35").trades == []
    assert len(run(unit_future, BASE, entry_end="09:40").trades) == 1
    assert run(unit_future, BASE, entry_start="09:45").trades == []


def test_no_signal_while_the_range_is_forming(unit_future):
    # Candles 2 and 3 close above the earlier highs, but inside the range window that is not a breakout.
    # Range: high 103.2, low 99 (width 4.2). Candle 5 (close 102.6) stays below 103.2; candle 7 closes 103.8 above
    # it after 103.2: signal at its 09:50 close, fill 103.8; target 103.8 + 4.2 = 108 not hit; square-off 105.2.
    rows = [(100, 101, 99, 100.5), (100.5, 103, 100, 102.9), (102.9, 103.2, 102, 103.1), *BASE[3:]]
    t = run(unit_future, rows).trades[0]
    assert (hm(t.entry_ts), t.entry_price, t.exit_reason, t.exit_price) == ("09:50", 103.8, "Square-off", 105.2)


def test_square_off_and_trailing_with_flat_target(unit_future):
    r = run(unit_future, BASE, target_type="none", target_value=None, trailing_stop_pct=1)
    # Trail 1% under the best high: after candle 8 (high 106) the trail is 104.94; candle 9 low 105 holds,
    # candle 10 is the 10:00 square-off -> exit at its open 105.2.
    t = r.trades[0]
    assert (t.exit_reason, t.exit_price) == ("Square-off", 105.2)


def _full_day(day: str, step: int, first: list[tuple], start: str = "09:15", until: str = "15:30"):
    """``first`` candles, then flat candles at the last close until ``until`` (inclusive)."""
    bars = make_bars(day, start, step, first)
    last = bars[-1].close
    t = bars[-1].ts + timedelta(minutes=step)
    end = datetime.fromisoformat(f"{day}T{until}")
    while t <= end:
        bars.append(Bar(t, last, last + 0.5, last - 0.5, last))
        t += timedelta(minutes=step)
    return bars


EXPIRY_ROWS = [(100, 101, 99, 100.5), (100.5, 102, 100.4, 101.8)]  # 15m: range = first candle; long at 09:30 close


@pytest.mark.parametrize(
    ("day", "exit_time", "reason"),
    [
        ("2026-12-29", "15:30", "Square-off"),  # last Tuesday of Dec 2026: NIFTY futures expire, stop at 15:30
        ("2026-12-22", "15:30", "Session end"),  # not an expiry: F&O runs to 15:45, the 15:40 square-off has no candle
    ],
)
def test_square_off_capped_on_expiry_day(day, exit_time, reason):
    inst = Instrument(symbol="NIFTY", exchange=Exchange.NFO, segment=Segment.FUTURES, lot_size=1)
    spec = build_template(
        "opening_range_breakout",
        name="ORB",
        instrument=inst,
        params={"timeframe": "15m", "opening_range_minutes": 15, "square_off": "15:40", "target_type": "none"},
    )
    r = run_backtest(spec, _full_day(day, 15, EXPIRY_ROWS), NO_COSTS)
    t = r.trades[0]
    assert (hm(t.entry_ts), t.entry_price) == ("09:45", 101.8)
    assert (hm(t.exit_ts), t.exit_reason) == (exit_time, reason)
    capped = any("Square-off moved earlier" in w for w in r.warnings)
    assert capped is (reason == "Square-off")


def test_no_trades_on_exchange_holiday(unit_future):
    # Fri 26 Jun 2026 is an NSE holiday (Muharram): candles in the file are ignored.
    r = run(unit_future, BASE, day="2026-06-26")
    assert r.trades == []
    assert any("holidays" in w and "No trades were taken" in w for w in r.warnings)
    # Turning the calendar off reproduces the old behaviour (the day is traded).
    r = run(
        unit_future, BASE, day="2026-06-26", config=BacktestConfig(apply_charges=False, use_exchange_calendar=False)
    )
    assert len(r.trades) == 1


def test_mcx_session_defaults_and_square_off():
    inst = Instrument(symbol="CRUDEOIL", exchange=Exchange.MCX, segment=Segment.COMMODITY_FUTURES, lot_size=1)
    spec = build_template(
        "opening_range_breakout",
        name="Crude ORB",
        instrument=inst,
        params={"timeframe": "15m", "opening_range_minutes": 30, "target_type": "none"},
    )
    assert (spec.session.start, spec.session.square_off) == ("09:00", "23:00")
    rows = [(5000, 5010, 4990, 5005), (5005, 5020, 5000, 5015), (5015, 5030, 5012, 5025)]  # range 4990-5020
    r = run_backtest(spec, _full_day(DAY, 15, rows, start="09:00", until="23:15"), NO_COSTS)
    t = r.trades[0]
    assert (hm(t.entry_ts), t.entry_price) == ("09:45", 5025)
    assert (hm(t.exit_ts), t.exit_reason, t.exit_price) == ("23:00", "Square-off", 5025)


def test_mcx_evening_only_session_on_nse_holiday():
    # 26 Jun 2026: NSE closed, MCX evening session only (from 17:00). The range forms 17:00-17:30.
    inst = Instrument(symbol="CRUDEOIL", exchange=Exchange.MCX, segment=Segment.COMMODITY_FUTURES, lot_size=1)
    spec = build_template(
        "opening_range_breakout",
        name="Crude ORB",
        instrument=inst,
        params={"timeframe": "15m", "opening_range_minutes": 30, "target_type": "none"},
    )
    rows = [(5000, 5010, 4990, 5005), (5005, 5020, 5000, 5015), (5015, 5030, 5012, 5025)]
    r = run_backtest(spec, _full_day("2026-06-26", 15, rows, start="17:00", until="23:15"), NO_COSTS)
    t = r.trades[0]
    assert (hm(t.entry_ts), hm(t.exit_ts), t.exit_reason) == ("17:45", "23:00", "Square-off")
    # MCX full holiday (2 Oct 2026): nothing trades.
    r = run_backtest(spec, _full_day("2026-10-02", 15, rows, start="09:00", until="10:00"), NO_COSTS)
    assert r.trades == []


def test_spec_shape_and_template_params(unit_future):
    s = orb(unit_future, buffer_type="points", buffer=2, range_min_pct=0.2, entry_end="11:00", timeframe="5m")
    assert s.template == "opening_range_breakout" and s.session.opening_range_minutes == 15
    long = s.entry.long
    assert long[0].op == "crosses_above" and long[0].right.ref == "range_high" and long[0].right.offset_points == 2
    assert s.entry.short[0].right.offset_points == -2
    assert long[1].left.ref == "range_width_pct" and long[2].op == "time_between" and long[2].end == "11:00"
    assert s.exits.stop_level.long.ref == "range_low" and s.exits.stop_level.short.ref == "range_high"
    assert s.exits.target_range_multiple == 1.0 and s.exits.flip is False
    assert s.template_params["buffer"] == 2 and s.template_params["start"] == "09:15"
    s = orb(unit_future, stop_type="pct", stop_value=0.5, target_type="pct", target_value=1, timeframe="5m")
    assert (s.exits.stop_pct, s.exits.target_pct, s.exits.stop_level) == (0.5, 1, None)


@pytest.mark.parametrize(
    ("params", "msg"),
    [
        ({"buffer": 6}, "0 to 5"),
        ({"range_min_pct": 2, "range_max_pct": 1}, "must not exceed"),
        ({"stop_type": "pct"}, "stop_value is required"),
        ({"target_type": "pct", "target_value": 50}, "0.05 to 20"),
        ({"target_type": "range_multiple", "target_value": 0.01}, "0.1 to 20"),
        ({"stop_type": "none", "target_type": "r_multiple", "target_value": 2}, "needs a stop"),
        ({"direction": "up"}, "Input should be"),
        ({"entry_end": "25:00"}, "HH:MM"),
        ({"unknown": 1}, "Extra inputs"),
    ],
)
def test_param_validation(params, msg):
    with pytest.raises(ValidationError, match=msg):
        OpeningRangeBreakoutParams(**params)


def test_values_of_types_without_a_value_are_cleared():
    p = OpeningRangeBreakoutParams(stop_value=1, target_type="none")
    assert (p.stop_value, p.target_value) == (None, None)


def test_range_must_fit_the_timeframe_and_session(unit_future):
    with pytest.raises(ValidationError, match="multiple of the 15m"):
        orb(unit_future, timeframe="15m", opening_range_minutes=20)
    with pytest.raises(ValidationError, match="before square_off"):
        orb(unit_future, timeframe="5m", opening_range_minutes=60, square_off="10:00")
