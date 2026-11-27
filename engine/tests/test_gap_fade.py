"""Gap fade on the DSL, with hand-computed fixtures.

Previous session (Tue 2 Jun 2026, 5m candles): 09:15 (99, 100.5, 98.5, 99.5) and 15:25 (99.6, 100.2, 99.4, 100).
The 15:25 candle is after the 15:15 square-off but still counts for the session: previous close = 100.

Gap-up day (Wed 3 Jun 2026), defaults (min gap 0.5%, max 3%, stop 1%, target = previous close, entries until
10:00, rules evaluated from the first candle):
    #  time   open   high   low    close
    1  09:15  101.2  101.5  100.9  101.3   gap = (101.2 - 100) / 100 = +1.2%; close 101.3 > 100 (gap open):
                                           SHORT signal at the 09:20 close
    2  09:20  101.3  101.4  100.6  100.8   short fills 101.3; stop 101.3 x 1.01 = 102.313; target 100
    3  09:25  100.8  100.9  99.9   100.1   low 99.9 <= 100: target -> +1.30 points
    4  09:30  100.1  100.3  99.8   100
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import make_bars
from pydantic import ValidationError
from test_opening_range_breakout import _full_day, hm

from algora_engine.backtest import BacktestConfig, run_backtest
from algora_engine.dsl import Instrument
from algora_engine.instruments import Exchange, Segment
from algora_engine.rules import Side
from algora_engine.templates import GapFadeParams, build_template

NO_COSTS = BacktestConfig(apply_charges=False)
PREV = [(99, 100.5, 98.5, 99.5)]
UP = [
    (101.2, 101.5, 100.9, 101.3),
    (101.3, 101.4, 100.6, 100.8),
    (100.8, 100.9, 99.9, 100.1),
    (100.1, 100.3, 99.8, 100),
]
DOWN = [
    (98.8, 99.1, 98.5, 98.7),  # gap -1.2%, close 98.7 < 100: LONG signal
    (98.7, 99.4, 98.6, 99.2),  # long fills 98.7; stop 97.713; target 100
    (99.2, 100.1, 99.1, 99.9),  # high 100.1 >= 100: target -> +1.30
    (99.9, 100, 99.7, 99.8),
]


def prev_day(day: str = "2026-06-02", close: float = 100.0):
    return make_bars(day, "09:15", 5, PREV) + make_bars(day, "15:25", 5, [(99.6, 100.2, 99.4, close)])


def gap_fade(inst, **params):
    params.setdefault("timeframe", "5m")
    return build_template("gap_fade", name="Gap", instrument=inst, params=params)


def run(inst, rows, config=NO_COSTS, day="2026-06-03", prev="2026-06-02", **params):
    bars = (prev_day(prev) if prev else []) + make_bars(day, "09:15", 5, rows)
    return run_backtest(gap_fade(inst, **params), bars, config)


def test_gap_up_is_faded_short_to_the_previous_close(unit_future):
    r = run(unit_future, UP)
    assert len(r.trades) == 1
    t = r.trades[0]
    assert t.side is Side.SHORT and t.day == date(2026, 6, 3)
    assert (hm(t.entry_ts), t.entry_price) == ("09:20", 101.3)
    assert (hm(t.exit_ts), t.exit_reason, t.exit_price) == ("09:25", "Target", 100)
    assert t.points == pytest.approx(1.3)
    # The previous day had no gap to measure against (first session in the data): reported, no trade.
    assert any("no previous session" in w for w in r.warnings)


def test_gap_down_is_faded_long(unit_future):
    t = run(unit_future, DOWN).trades[0]
    assert t.side is Side.LONG and t.entry_price == 98.7
    assert (t.exit_reason, t.exit_price, t.points) == ("Target", 100, pytest.approx(1.3))


@pytest.mark.parametrize(
    ("open_", "trades"),
    [(100.3, 0), (100.5, 1), (103.0, 1), (103.2, 0)],  # 0.3% too small, 0.5% and 3% at the bounds, 3.2% too big
)
def test_min_and_max_gap(unit_future, open_, trades):
    rows = [(open_, open_ + 0.3, open_ - 0.1, open_ + 0.1), *UP[1:]]
    assert len(run(unit_future, rows).trades) == trades


def test_fade_direction(unit_future):
    assert run(unit_future, UP, fade="gap_down").trades == []
    assert len(run(unit_future, UP, fade="gap_up").trades) == 1
    assert run(unit_future, DOWN, fade="gap_up").trades == []


def test_require_unfilled(unit_future):
    # Gap up +1.2%, but the first candle already closes at 99.8, below the previous close: the gap has filled.
    rows = [(101.2, 101.5, 99.6, 99.8), (99.8, 100.4, 99.5, 99.9), (99.9, 100, 99.6, 99.7), (99.7, 99.9, 99.5, 99.6)]
    assert run(unit_future, rows).trades == []
    # Without the check the short fills at 99.8, where the 100 target is already passed: it closes at that open.
    t = run(unit_future, rows, require_unfilled=False).trades[0]
    assert (t.entry_price, t.exit_reason, t.exit_price, t.points) == (99.8, "Target", 99.8, 0)


def test_stop_first_when_stop_and_target_touch_same_candle(unit_future):
    # Short at 101.3: stop 102.313 and target 100 both inside candle 2's 99.5-102.5 range -> stop.
    rows = [UP[0], (101.3, 102.5, 99.5, 101), *UP[2:]]
    t = run(unit_future, rows).trades[0]
    assert (t.exit_reason, t.exit_price) == ("Stop", pytest.approx(102.313))
    assert t.points == pytest.approx(-1.013)


def test_stop_at_first_candle_extreme_and_gap_through(unit_future):
    # Short stop at the first candle's high, 101.5. Candle 3 opens 101.9, already above: exit at that open.
    rows = [UP[0], (101.3, 101.45, 101.0, 101.4), (101.9, 102.0, 101.6, 101.8), UP[3]]
    t = run(unit_future, rows, stop_type="first_candle_extreme").trades[0]
    assert (t.exit_reason, t.exit_price, t.points) == ("Stop", 101.9, pytest.approx(-0.6))


def test_percent_and_r_targets(unit_future):
    # Target 1%: 101.3 x 0.99 = 100.287; candle 2 low 100.6 misses, candle 3 low 99.9 hits.
    t = run(unit_future, UP, target_type="pct", target_value=1).trades[0]
    assert (hm(t.exit_ts), t.exit_price) == ("09:25", pytest.approx(100.287))
    # 1R with the 1% stop: risk 1.013, target 100.287 (same level).
    t = run(unit_future, UP, target_type="r_multiple", target_value=1).trades[0]
    assert t.exit_price == pytest.approx(100.287)


def test_entry_window(unit_future):
    # The first candle closes at 09:20.
    assert run(unit_future, UP, entry_end="09:19").trades == []
    assert len(run(unit_future, UP, entry_end="09:20").trades) == 1
    assert run(unit_future, UP, entry_start="09:25").trades[0].entry_price == 100.8  # signal on candle 2's close


def test_previous_session_missing_from_data(unit_future):
    # Data has Mon 1 Jun and Wed 3 Jun: Tue 2 Jun (a trading day) is missing, so the gap is unknown.
    r = run(unit_future, UP, prev="2026-06-01")
    assert r.trades == []
    assert any("previous trading session is missing" in w for w in r.warnings)


def test_previous_session_across_a_holiday(unit_future):
    # Thu 25 Jun 2026 -> Mon 29 Jun 2026 (Fri 26 Jun is the Muharram holiday): Thursday is the previous session.
    r = run(unit_future, UP, day="2026-06-29", prev="2026-06-25")
    assert len(r.trades) == 1 and r.trades[0].points == pytest.approx(1.3)
    assert not any("missing" in w for w in r.warnings)


def test_previous_session_unverified_in_a_year_without_holiday_list(unit_future):
    # 2027 has no holiday list yet: Thu 7 Jan -> Mon 11 Jan skips Fri 8 Jan, which cannot be checked.
    r = run(unit_future, UP, day="2027-01-11", prev="2027-01-07")
    assert len(r.trades) == 1
    assert any("could not be checked" in w for w in r.warnings)


def test_no_trades_on_holiday(unit_future):
    r = run(unit_future, UP, day="2026-06-26", prev="2026-06-25")
    assert r.trades == []
    assert any("No trades were taken" in w for w in r.warnings)


def test_square_off_capped_on_expiry_day():
    # Mon 28 Dec 2026 closes at 100; Tue 29 Dec (NIFTY monthly futures expiry) gaps down 1.2%: long 98.7,
    # no target, no stop, square-off 15:40 capped at 15:30 -> exit at the 15:30 open, 99.2 (flat candles).
    inst = Instrument(symbol="NIFTY", exchange=Exchange.NFO, segment=Segment.FUTURES, lot_size=1)
    spec = gap_fade(inst, square_off="15:40", target_type="none", stop_type="none")
    bars = prev_day("2026-12-28") + _full_day("2026-12-29", 5, DOWN[:2])
    t = run_backtest(spec, bars, NO_COSTS).trades[0]
    assert (hm(t.entry_ts), t.entry_price) == ("09:20", 98.7)
    assert (hm(t.exit_ts), t.exit_reason, t.exit_price) == ("15:30", "Square-off", 99.2)


def test_mcx_gap_against_previous_mcx_session():
    # MCX: previous close is the last candle of the previous MCX day (23:25 here); default start 09:00.
    inst = Instrument(symbol="GOLDM", exchange=Exchange.MCX, segment=Segment.COMMODITY_FUTURES, lot_size=1)
    spec = gap_fade(inst)
    assert (spec.session.start, spec.template_params["entry_end"]) == ("09:00", "09:45")
    bars = make_bars("2026-06-02", "23:25", 5, [(99.8, 100.2, 99.7, 100)]) + make_bars("2026-06-03", "09:00", 5, UP)
    t = run_backtest(spec, bars, NO_COSTS).trades[0]
    assert (hm(t.entry_ts), t.entry_price, t.exit_price) == ("09:05", 101.3, 100)


def test_spec_shape(unit_future):
    s = gap_fade(unit_future)
    assert s.template == "gap_fade" and s.session.signals_from == "session_start"
    assert [c.op for c in s.entry.short] == ["gte", "lte", "gt", "time_between"]
    assert s.entry.short[-1].end == "10:00" and s.entry.long[0].right.value == -0.5
    assert s.exits.target_level.long.kind == "prev_session" and s.exits.stop_pct == 1.0
    s = gap_fade(unit_future, max_gap_pct=None, require_unfilled=False, fade="gap_down")
    assert s.entry.short == [] and [c.op for c in s.entry.long] == ["lte", "time_between"]


@pytest.mark.parametrize(
    ("params", "msg"),
    [
        ({"min_gap_pct": 2, "max_gap_pct": 1}, "must not be below"),
        ({"stop_type": "pct", "stop_value": None}, "stop_value is required"),
        ({"stop_type": "none", "target_type": "r_multiple", "target_value": 1}, "needs a stop"),
        ({"fade": "sideways"}, "Input should be"),
    ],
)
def test_param_validation(params, msg):
    with pytest.raises(ValidationError, match=msg):
        GapFadeParams(**params)
