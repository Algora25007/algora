"""First Candle Cross on the DSL, with hand-computed fixtures.

Base day (Wed 3 Jun 2026, 15m candles from 09:15, buffer 0):
    #  time   open   high   low    close
    1  09:15  100    102    99     101     first candle: upper 102, lower 99
    2  09:30  101    102.5  100.5  102     close 102 is not above 102: no signal
    3  09:45  102    104    101.5  103.5   closes above 102 after 102 <= 102: LONG signal
    4  10:00  104    105    103    104.5   long fills at this open, 104 (not at 103.5, the signal close)
    5  10:15  104.5  106    104    105.5
    6  10:30  106    107    105    106.5   square-off 10:30: exit at the open, 106 -> +2.00 points
"""

from __future__ import annotations

import pytest
from conftest import make_bars

from algora_engine.backtest import BacktestConfig, run_backtest
from algora_engine.candles import Bar
from algora_engine.dsl import Instrument
from algora_engine.instruments import Exchange, Segment
from algora_engine.rules import Side
from algora_engine.templates import first_candle_cross

DAY = "2026-06-03"
BASE = [
    (100, 102, 99, 101),
    (101, 102.5, 100.5, 102),
    (102, 104, 101.5, 103.5),
    (104, 105, 103, 104.5),
    (104.5, 106, 104, 105.5),
    (106, 107, 105, 106.5),
]
NO_COSTS = BacktestConfig(apply_charges=False)


def spec(inst, **kw):
    kw.setdefault("square_off", "10:30")
    return first_candle_cross(name="FCB", instrument=inst, timeframe="15m", **kw)


def run(inst, rows, config=NO_COSTS, day=DAY, **kw):
    return run_backtest(spec(inst, **kw), make_bars(day, "09:15", 15, rows), config)


def test_entry_fills_at_next_open_and_squares_off(unit_future):
    r = run(unit_future, BASE)
    assert len(r.trades) == 1
    t = r.trades[0]
    assert t.side is Side.LONG
    assert t.entry_ts.strftime("%H:%M") == "10:00" and t.entry_price == 104
    assert t.exit_ts.strftime("%H:%M") == "10:30" and t.exit_price == 106
    assert t.exit_reason == "Square-off"
    assert t.points == pytest.approx(2.0)
    assert r.summary["net_pnl"] == 2.0


def test_stop_assumed_first_when_stop_and_target_touch_same_candle(unit_future):
    # Entry 104; stop 1% = 102.96, target 1% = 105.04. Candle 4 spans 102.5-105.5: both touched -> stop.
    rows = [*BASE[:3], (104, 105.5, 102.5, 104), *BASE[4:]]
    r = run(unit_future, rows, stop_pct=1, target_pct=1)
    t = r.trades[0]
    assert t.exit_reason == "Stop"
    assert t.exit_price == pytest.approx(102.96)
    assert t.points == pytest.approx(-1.04)


def test_target_hits_when_stop_not_touched(unit_future):
    rows = [*BASE[:3], (104, 105.5, 103.5, 105), *BASE[4:]]
    t = run(unit_future, rows, stop_pct=1, target_pct=1).trades[0]
    assert t.exit_reason == "Target" and t.exit_price == pytest.approx(105.04)


def test_gap_through_stop_fills_at_open(unit_future):
    # Entry 104 (stop 102.96). Candle 5 opens at 102, already beyond the stop: fill 102, not 102.96.
    rows = [*BASE[:3], (104, 104.5, 103.5, 103.8), (102, 102.5, 101, 101.5), BASE[5]]
    t = run(unit_future, rows, stop_pct=1).trades[0]
    assert t.exit_reason == "Stop" and t.exit_price == 102 and t.points == pytest.approx(-2)


def test_gap_through_target_fills_at_open(unit_future):
    rows = [*BASE[:3], (104, 104.5, 103.5, 104.2), (106, 106.5, 105.5, 106), BASE[5]]
    t = run(unit_future, rows, target_pct=1).trades[0]
    assert t.exit_reason == "Target" and t.exit_price == 106


def test_slippage_points_and_pct(unit_future):
    # Points: buy 104 + 0.5 = 104.5, square-off sell 106 - 0.5 = 105.5 -> +1.00
    t = run(unit_future, BASE, BacktestConfig(apply_charges=False, slippage_points=0.5)).trades[0]
    assert (t.entry_price, t.exit_price) == (104.5, 105.5)
    # Pct 0.1%: buy 104.104 -> rounded up to tick 104.15; sell 105.894 -> down to 105.85 -> +1.70
    t = run(unit_future, BASE, BacktestConfig(apply_charges=False, slippage_pct=0.1)).trades[0]
    assert t.entry_price == pytest.approx(104.15) and t.exit_price == pytest.approx(105.85)
    assert t.points == pytest.approx(1.70)


def test_short_side_and_flip(unit_future):
    # Long fills 104 at 10:00. Candle 5 closes 98.5 < 99 after 104.5 >= 99: SHORT signal.
    # Candle 6 open 98: flip exit long at 98 (-6), short at 98. Candle 7 at 10:45 = square-off at open 97 (+1).
    rows = [*BASE[:4], (104.5, 104.5, 98, 98.5), (98, 98.5, 97, 97.5), (97, 97.5, 96, 96.5)]
    r = run(unit_future, rows, square_off="10:45", max_entries_per_day=2)
    assert [(t.side, t.entry_price, t.exit_price, t.exit_reason) for t in r.trades] == [
        (Side.LONG, 104, 98, "Flip"),
        (Side.SHORT, 98, 97, "Square-off"),
    ]
    assert r.summary["net_pnl"] == pytest.approx(-5)


def test_flip_respects_max_entries(unit_future):
    rows = [*BASE[:4], (104.5, 104.5, 98, 98.5), (98, 98.5, 97, 97.5), (97, 97.5, 96, 96.5)]
    r = run(unit_future, rows, square_off="10:45", max_entries_per_day=1)
    assert [(t.side, t.exit_reason, t.exit_price) for t in r.trades] == [(Side.LONG, "Flip", 98)]


def test_no_flip_ignores_opposite_signal(unit_future):
    rows = [*BASE[:4], (104.5, 104.5, 98, 98.5), (98, 98.5, 97, 97.5), (97, 97.5, 96, 96.5)]
    r = run(unit_future, rows, square_off="10:45", flip=False, max_entries_per_day=2)
    assert [(t.side, t.exit_reason, t.exit_price) for t in r.trades] == [(Side.LONG, "Square-off", 97)]


def test_short_stop_and_target_mirror(unit_future):
    # First candle 100/102/99/101; candle 2 closes 98.5 -> short; fills candle 3 open 98.
    # Short stop 1% = 98.98, target 1% = 97.02.
    rows = [(100, 102, 99, 101), (100, 100.5, 98, 98.5), (98, 98.5, 97.5, 98.2), (98.2, 99.2, 98, 99), (99, 99, 98, 98)]
    t = run(unit_future, rows, stop_pct=1, target_pct=1).trades[0]
    assert t.side is Side.SHORT and t.exit_reason == "Stop" and t.exit_price == pytest.approx(98.98)
    rows[3] = (98.2, 98.5, 96.9, 97)
    t = run(unit_future, rows, stop_pct=1, target_pct=1).trades[0]
    assert t.exit_reason == "Target" and t.exit_price == pytest.approx(97.02)
    # gap-through for short: open below target
    rows[3] = (96.5, 97, 96, 96.5)
    t = run(unit_future, rows, stop_pct=1, target_pct=1).trades[0]
    assert t.exit_reason == "Target" and t.exit_price == 96.5
    rows[3] = (99.5, 100, 99, 99.5)
    t = run(unit_future, rows, stop_pct=1, target_pct=1).trades[0]
    assert t.exit_reason == "Stop" and t.exit_price == 99.5


def test_trailing_stop_ratchets_and_uses_previous_candles_only(unit_future):
    # Long fills at 104 on candle 4 (high 105). Trail 2%: after candle 4 the best is 105 -> trail 102.9.
    # Candle 5 high 106 low 103: 103 > 102.9, no exit; best becomes 106 -> trail 103.88.
    # Candle 6 low 103.5 touches 103.88 -> Trailing stop at 103.88.
    rows = [*BASE[:4], (104.5, 106, 103, 105.5), (105.5, 105.6, 103.5, 104), (104, 104, 103, 103)]
    t = run(unit_future, rows, trailing_stop_pct=2, square_off="10:45").trades[0]
    assert t.exit_reason == "Trailing stop"
    assert t.exit_price == pytest.approx(106 * 0.98)


def test_fixed_stop_binds_until_trail_is_tighter(unit_future):
    # stop 1% = 102.96 vs trail 5% of 105 = 99.75 -> fixed stop binds, reason "Stop".
    rows = [*BASE[:4], (104.5, 104.6, 102.5, 103), BASE[5]]
    t = run(unit_future, rows, trailing_stop_pct=5, stop_pct=1).trades[0]
    assert t.exit_reason == "Stop" and t.exit_price == pytest.approx(102.96)


def test_trailing_stop_on_short(unit_future):
    # Short fills 98 (candle 3). Candle 3 low 97.5 -> trail 2% = 99.45. Candle 4 high 99.5 -> Trailing stop 99.45.
    rows = [(100, 102, 99, 101), (100, 100.5, 98, 98.5), (98, 98.5, 97.5, 98.2), (98.2, 99.5, 98, 99), (99, 99, 98, 98)]
    t = run(unit_future, rows, trailing_stop_pct=2).trades[0]
    assert t.side is Side.SHORT and t.exit_reason == "Trailing stop"
    assert t.exit_price == pytest.approx(97.5 * 1.02)


def test_reentry_after_stop_up_to_max_entries(unit_future):
    # Entry 1 at 104 stopped at 102.96 on candle 4; candle 5 re-crosses 102 (close 103 after 102.5 close?)
    rows = [
        (100, 102, 99, 101),
        (101, 102, 100.5, 101.5),
        (101.5, 103, 101, 102.5),  # LONG signal (102.5 > 102, prev 101.5 <= 102)
        (104, 104.2, 101.5, 101.8),  # fill 104, low 101.5 < 102.96 -> Stop; close 101.8 below 102
        (101.8, 103.5, 101.5, 103),  # re-cross -> LONG signal
        (103.2, 104, 103, 103.8),  # fill 103.2
        (104, 104, 103.5, 103.6),  # square-off 10:45 at 104
    ]
    r = run(unit_future, rows, stop_pct=1, max_entries_per_day=2, square_off="10:45")
    assert [(t.entry_price, t.exit_reason) for t in r.trades] == [(104, "Stop"), (103.2, "Square-off")]
    r = run(unit_future, rows, stop_pct=1, max_entries_per_day=1, square_off="10:45")
    assert len(r.trades) == 1


def test_buffer_moves_levels(unit_future):
    # buffer 1%: upper = 102 * 1.01 = 103.02 -> candle 3 close 103.5 still crosses, entry 104 at 10:00.
    assert run(unit_future, BASE, buffer_pct=1).trades[0].entry_price == 104
    # buffer 2%: upper 104.04 -> candle 3 (103.5) no; candle 4 closes 104.5 after 103.5 -> entry 104.5 at 10:15.
    t = run(unit_future, BASE, buffer_pct=2).trades[0]
    assert t.entry_ts.strftime("%H:%M") == "10:15" and t.entry_price == 104.5
    # buffer 5%: upper 107.1, never crossed.
    assert run(unit_future, BASE, buffer_pct=5).trades == []


def test_signal_on_last_candle_before_square_off_never_fills(unit_future):
    # Signal at 09:45; the 10:00 candle is the square-off candle, so the pending entry is cancelled.
    assert run(unit_future, BASE, square_off="10:00").trades == []
    # Data ends right after the signal candle: nothing to fill against.
    assert run(unit_future, BASE[:3], square_off="10:30").trades == []


def test_session_end_closes_at_last_close_when_no_square_off_candle(unit_future):
    r = run(unit_future, BASE[:5], square_off="15:15")
    t = r.trades[0]
    assert t.exit_reason == "Session end" and t.exit_price == 105.5


def test_day_skipped_when_first_candle_at_or_after_square_off(unit_future):
    bars = make_bars(DAY, "10:30", 15, BASE)
    r = run_backtest(spec(unit_future), bars, NO_COSTS)
    assert r.trades == []


def test_candles_before_session_start_are_ignored(unit_future):
    pre = make_bars(DAY, "09:00", 15, [(50, 200, 10, 60)])
    bars = pre + make_bars(DAY, "09:15", 15, BASE)
    r = run_backtest(spec(unit_future), bars, NO_COSTS)
    assert r.trades[0].entry_price == 104


def test_multi_day_resets_levels_and_counts(unit_future):
    bars = make_bars("2026-06-03", "09:15", 15, BASE) + make_bars("2026-06-04", "09:15", 15, BASE)
    r = run_backtest(spec(unit_future), bars, NO_COSTS)
    assert len(r.trades) == 2
    assert [d["day"] for d in r.days] == ["2026-06-03", "2026-06-04"]
    assert r.summary["days_traded"] == 2 and r.summary["net_pnl"] == 4.0


def test_position_closed_at_session_end_before_next_day(unit_future):
    bars = make_bars("2026-06-03", "09:15", 15, BASE[:5]) + make_bars("2026-06-04", "09:15", 15, BASE)
    r = run_backtest(spec(unit_future, square_off="15:15"), bars, NO_COSTS)
    assert r.trades[0].exit_reason == "Session end" and r.trades[0].exit_ts.date().isoformat() == "2026-06-03"


def test_long_only_direction(unit_future):
    rows = [(100, 102, 99, 101), (100, 100.5, 98, 98.5), (98, 98.5, 97.5, 98.2), (98, 98, 97, 97)]
    s = first_candle_cross(name="L", instrument=unit_future, direction="long", square_off="10:30")
    assert run_backtest(s, make_bars(DAY, "09:15", 15, rows), NO_COSTS).trades == []
    with pytest.raises(ValueError):
        first_candle_cross(name="x", instrument=unit_future, direction="up")
    with pytest.raises(ValueError):
        first_candle_cross(name="x", instrument=unit_future, buffer_pct=6)


def test_quantity_in_lots_uses_lot_table():
    inst = Instrument(symbol="NIFTY", exchange=Exchange.NFO, segment=Segment.FUTURES)
    r = run(inst, BASE, quantity_lots=2)
    t = r.trades[0]
    assert t.qty == 130  # 2 lots x 65 (NSE/FAOP/70616)
    assert t.gross_pnl == pytest.approx(260)
    assert r.meta["lot_sizes"] == {"65": "NSE/FAOP/70616 (3 Oct 2025)"}


def test_charges_are_deducted_per_trade():
    inst = Instrument(symbol="NIFTY", exchange=Exchange.NFO, segment=Segment.FUTURES)
    r = run(inst, BASE, BacktestConfig())
    t = r.trades[0]
    # buy 104*65 = 6760, sell 106*65 = 6890 (3 Jun 2026)
    # brokerage 0.03%: 2.028 + 2.067 = 4.095 -> 4.10 ; STT 0.05% of 6890 = 3.445 -> 3.45
    # txn 0.00183% of 13650 = 0.2498 -> 0.25 ; SEBI 0.01365 -> 0.01 ; stamp 0.002% of 6760 = 0.1352 -> 0.14
    # GST 18% of (4.10 + 0.25 + 0.01) = 0.7848 -> 0.78 ; total 8.73
    assert t.charges is not None and float(t.charges.total) == pytest.approx(8.73)
    assert t.net_pnl == pytest.approx(130 - 8.73)
    assert r.summary["total_charges"] == pytest.approx(8.73)
    assert "[STT-2026]" in r.meta["charges_sources"]


def test_trades_must_be_time_ordered_in_trader(unit_future):
    from algora_engine.execution import SimulatedFills
    from algora_engine.trader import Trader

    tr = Trader(spec(unit_future), SimulatedFills(), qty_for_day=lambda d: 1)
    bars = make_bars(DAY, "09:15", 15, BASE)
    tr.on_bar(bars[1])
    with pytest.raises(ValueError):
        tr.on_bar(bars[0])


def test_trader_streams_same_result_as_backtest(unit_future):
    """The trader is event-driven: feeding candles one by one equals the batch backtest (paper/live reuse)."""
    from algora_engine.execution import SimulatedFills
    from algora_engine.trader import Trader

    tr = Trader(spec(unit_future), SimulatedFills(), qty_for_day=lambda d: 1)
    out = []
    for b in make_bars(DAY, "09:15", 15, BASE):
        out += tr.on_bar(b)
    out += tr.finish()
    assert [t.as_dict() for t in out] == [t.as_dict() for t in run(unit_future, BASE).trades]


def test_bar_minute_property():
    b = make_bars(DAY, "09:15", 15, BASE)[0]
    assert isinstance(b, Bar) and b.minute == 555
