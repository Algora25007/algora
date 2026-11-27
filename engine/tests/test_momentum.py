"""Intraday momentum on the DSL (EMA crossover / N-candle breakout, optional VWAP), with hand-computed fixtures.

EMA crossover, fast 2 / slow 3 on the close (Wed 3 Jun 2026, 5m from 09:15, square-off 10:00):
    #   time   open   high   low    close   EMA2      EMA3     EMA2-EMA3
    1   09:15  100    100.5  99.5   100     -         -        -        (first candle: not evaluated)
    2   09:20  100    100.2  98.8   99      99.5      -        -
    3   09:25  99     99.2   97.8   98      98.5      99       -0.5
    4   09:30  98     99.3   97.9   99      98.8333   99       -0.1667
    5   09:35  99     101.2  98.9   101     100.2778  100      +0.2778  crosses above: LONG signal
    6   09:40  101.2  103.3  101    103     102.0926  101.5    +0.5926  long fills 101.2
    7   09:45  103    103.4  101.8  102     102.0309  101.75   +0.2809
    8   09:50  102    102.2  99.8   100     100.6770  100.875  -0.1980  crosses below: SHORT signal
    9   09:55  99.9   100    97.8   98      98.8923   99.4375  -0.5452  flip: long out 99.9 (-1.3), short in 99.9
   10   10:00  98.2   98.5   98     98.3                                square-off: short out 98.2 (+1.7)
EMA seed = SMA of the first `length` closes, then alpha = 2 / (length + 1): 2/3 for EMA2, 1/2 for EMA3.
"""

from __future__ import annotations

import pytest
from conftest import make_bars
from pydantic import ValidationError
from test_opening_range_breakout import _full_day, hm

from algora_engine.backtest import BacktestConfig, BacktestError, run_backtest
from algora_engine.candles import Bar
from algora_engine.dsl import Instrument
from algora_engine.instruments import Exchange, Segment
from algora_engine.rules import Side
from algora_engine.templates import MomentumParams, build_template

DAY = "2026-06-03"
ROWS = [
    (100, 100.5, 99.5, 100),
    (100, 100.2, 98.8, 99),
    (99, 99.2, 97.8, 98),
    (98, 99.3, 97.9, 99),
    (99, 101.2, 98.9, 101),
    (101.2, 103.3, 101, 103),
    (103, 103.4, 101.8, 102),
    (102, 102.2, 99.8, 100),
    (99.9, 100, 97.8, 98),
    (98.2, 98.5, 98, 98.3),
]
NO_COSTS = BacktestConfig(apply_charges=False)
EMA = {"fast_length": 2, "slow_length": 3, "stop_type": "none", "square_off": "10:00"}


def momentum(inst, **params):
    params.setdefault("timeframe", "5m")
    return build_template("momentum", name="Mom", instrument=inst, params=params)


def run(inst, rows=ROWS, config=NO_COSTS, volumes=None, **params):
    bars = make_bars(DAY, "09:15", 5, rows)
    if volumes is not None:
        bars = [Bar(b.ts, b.open, b.high, b.low, b.close, v) for b, v in zip(bars, volumes, strict=True)]
    return run_backtest(momentum(inst, **params), bars, config)


def summary(trades):
    return [(t.side, hm(t.entry_ts), t.entry_price, hm(t.exit_ts), t.exit_reason, round(t.points, 4)) for t in trades]


def test_ema_cross_long_then_flip_short(unit_future):
    r = run(unit_future, **EMA)
    assert summary(r.trades) == [
        (Side.LONG, "09:40", 101.2, "09:55", "Flip", -1.3),
        (Side.SHORT, "09:55", 99.9, "10:00", "Square-off", 1.7),
    ]


def test_stop_then_fresh_entry_on_the_opposite_cross(unit_future):
    # 1% stop: long 101.2 -> 100.188; candle 8's low 99.8 hits it. The same candle's close crosses below: short
    # 99.9 at candle 9 (stop 100.899 not touched), out at the 10:00 square-off, 98.2.
    r = run(unit_future, **{**EMA, "stop_type": "pct", "stop_value": 1})
    assert summary(r.trades) == [
        (Side.LONG, "09:40", 101.2, "09:50", "Stop", -1.012),
        (Side.SHORT, "09:55", 99.9, "10:00", "Square-off", 1.7),
    ]
    assert len(run(unit_future, **{**EMA, "stop_type": "pct", "stop_value": 1, "max_entries_per_day": 1}).trades) == 1


def test_long_only_exits_on_the_opposite_cross(unit_future):
    r = run(unit_future, **EMA, direction="long")
    assert summary(r.trades) == [(Side.LONG, "09:40", 101.2, "09:55", "Exit rule", -1.3)]
    # Without the exit rule the long is held to the square-off (10:00 open, 98.2).
    r = run(unit_future, **EMA, direction="long", exit_on_opposite_signal=False)
    assert summary(r.trades) == [(Side.LONG, "09:40", 101.2, "10:00", "Square-off", -3.0)]


def test_no_flip_closes_on_the_exit_rule_and_enters_the_reverse(unit_future):
    # flip=False but exit_on_opposite_signal: the long closes on the cross below; the flat book then takes the short.
    r = run(unit_future, **EMA, flip=False)
    assert [t.exit_reason for t in r.trades] == ["Exit rule", "Square-off"]


def test_target_and_same_bar_stop(unit_future):
    # Target 1.5%: 101.2 x 1.015 = 102.718; candle 6's high 103.3 reaches it.
    t = run(unit_future, **{**EMA, "target_type": "pct", "target_value": 1.5}).trades[0]
    assert (hm(t.exit_ts), t.exit_reason, t.exit_price) == ("09:40", "Target", pytest.approx(102.718))
    # Stop 0.5% (100.694) and target 1.5% both inside candle 6 (low 100.6): stop first.
    rows = [*ROWS[:5], (101.2, 103.3, 100.6, 103), *ROWS[6:]]
    t = run(
        unit_future, rows, **{**EMA, "stop_type": "pct", "stop_value": 0.5, "target_type": "pct", "target_value": 1.5}
    )
    assert (t.trades[0].exit_reason, t.trades[0].exit_price) == ("Stop", pytest.approx(100.694))


def test_vwap_filter(unit_future):
    # Volumes 1000 except candle 3 (10,000). Typical prices (H+L+C)/3: 100, 99.333, 98.333, 98.733, 100.367,
    # 102.433, 102.4, 100.667. VWAP at candle 5 = 1,381,767 / 14,000 = 98.698 < close 101: the long is allowed.
    # VWAP at candle 8 = 1,687,267 / 17,000 = 99.251 > close 100: the short is blocked. The long still closes on
    # the opposite cross (exit rules ignore the filter).
    vols = [1000, 1000, 10_000, 1000, 1000, 1000, 1000, 1000, 1000, 1000]
    r = run(unit_future, volumes=vols, vwap_filter=True, **EMA)
    assert summary(r.trades) == [(Side.LONG, "09:40", 101.2, "09:55", "Exit rule", -1.3)]
    with pytest.raises(BacktestError, match="volume"):
        run(unit_future, vwap_filter=True, **EMA)


def test_n_bar_breakout(unit_future):
    # highest(high, 3) of the 3 candles before: candle 4 (close 101.3) vs max(101, 101.5, 101.2) = 101.5 -> no;
    # candle 5 (close 102) vs max(101.5, 101.2, 101.4) = 101.5, after 101.3 <= 101.5 -> LONG; fills 102.1.
    rows = [
        (100, 101, 99, 100.5),
        (100.5, 101.5, 100, 101),
        (101, 101.2, 100.2, 100.8),
        (100.8, 101.4, 100.5, 101.3),
        (101.3, 102.2, 101.1, 102),
        (102.1, 102.5, 101.9, 102.3),
        (102.3, 102.4, 102.0, 102.2),
    ]
    p = {"signal": "breakout", "breakout_length": 3, "stop_type": "none", "square_off": "09:45"}
    r = run(unit_future, rows, **p)
    assert summary(r.trades) == [(Side.LONG, "09:40", 102.1, "09:45", "Square-off", 0.2)]


def test_breakout_scope_session_ignores_yesterday(unit_future):
    # Yesterday's last candles have highs of 105. highest(high, 1) at today's second candle is the previous
    # candle's high, today's first candle (100.5), in both scopes. At today's FIRST candle
    # continuous scope still sees yesterday (105) while session scope has nothing yet.
    from algora_engine.dsl import IndicatorOperand
    from algora_engine.rules import RuleSignals

    prev = make_bars("2026-06-02", "15:10", 5, [(104, 105, 103.5, 104.5)] * 4)
    today = make_bars(DAY, "09:15", 5, ROWS[:2])
    for scope, first in (("continuous", 105.0), ("session", None)):
        spec = momentum(unit_future, signal="breakout", breakout_length=1, breakout_scope=scope)
        key = IndicatorOperand(name="highest", length=1, field="high", scope=scope).key
        sig = RuleSignals(spec)
        for b in prev:
            sig.on_bar(b)
        sig.on_bar(today[0])
        assert sig._prev[1][key] == first
        sig.on_bar(today[1])
        assert sig._prev[1][key] == 100.5


def test_ema_runs_continuously_across_days(unit_future):
    # Feeding yesterday's closes warms the EMAs up, so today's values differ from a cold start.
    from algora_engine.dsl import IndicatorOperand
    from algora_engine.rules import RuleSignals

    spec = momentum(unit_future, **EMA)
    sig = RuleSignals(spec)
    for b in make_bars("2026-06-02", "15:00", 5, [(110, 110, 110, 110)] * 3):
        sig.on_bar(b)
    for b in make_bars(DAY, "09:15", 5, ROWS[:1]):
        sig.on_bar(b)
    ema3 = IndicatorOperand(name="ema", length=3)
    # EMA3 after 110, 110, 110, 100 = 110 + 0.5 x (100 - 110) = 105.
    assert sig._prev[1][ema3.key] == pytest.approx(105)


def test_square_off_on_expiry_day_and_holiday():
    inst = Instrument(symbol="BANKNIFTY", exchange=Exchange.NFO, segment=Segment.FUTURES, lot_size=1)
    spec = momentum(inst, fast_length=2, slow_length=3, stop_type="none", square_off="15:40", direction="long")
    # Tue 29 Dec 2026 is the BANKNIFTY monthly futures expiry: square-off capped at 15:30.
    bars = _full_day("2026-12-29", 5, ROWS[:6])
    t = run_backtest(spec, bars, NO_COSTS).trades[0]
    assert (hm(t.entry_ts), hm(t.exit_ts), t.exit_reason) == ("09:40", "15:30", "Square-off")
    # 25 Dec 2026 (Christmas): no trades.
    bars = [Bar(b.ts.replace(day=25), b.open, b.high, b.low, b.close) for b in bars]
    assert run_backtest(spec, bars, NO_COSTS).trades == []


def test_entry_window_and_spec_shape(unit_future):
    assert run(unit_future, **EMA, entry_end="09:35").trades == []  # the long signal candle closes 09:40
    s = momentum(unit_future, vwap_filter=True, entry_start="09:30")
    assert s.template == "momentum" and s.risk.max_entries_per_day == 3 and s.exits.stop_pct == 1.0
    assert [c.op for c in s.entry.long] == ["crosses_above", "gt", "time_between"]
    assert s.entry.long[0].left.length == 9 and s.entry.long[0].right.length == 21
    assert s.exits.conditions.long[0].op == "crosses_below" and len(s.exits.conditions.long) == 1
    s = momentum(unit_future, direction="short", signal="breakout")
    assert s.entry.long == [] and s.exits.conditions.long == [] and s.exits.conditions.short[0].op == "crosses_above"
    assert s.entry.short[0].right.name == "lowest" and s.entry.short[0].right.scope == "session"


@pytest.mark.parametrize(
    ("params", "msg"),
    [
        ({"fast_length": 21, "slow_length": 9}, "shorter"),
        ({"stop_type": "none", "target_type": "r_multiple", "target_value": 2}, "needs a stop"),
        ({"target_type": "pct"}, "target_value is required"),
        ({"signal": "rsi"}, "Input should be"),
    ],
)
def test_param_validation(params, msg):
    with pytest.raises(ValidationError, match=msg):
        MomentumParams(**params)


def test_breakout_params_do_not_need_ordered_emas():
    assert MomentumParams(signal="breakout", fast_length=30, slow_length=10).signal == "breakout"
