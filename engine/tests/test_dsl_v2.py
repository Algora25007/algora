"""Schema v2 additions: operands, time conditions, level exits, exit conditions, upgrade from v1."""

import json

import pytest
from conftest import make_bars
from pydantic import ValidationError
from test_dsl import VALID

from algora_engine import calendar as cal
from algora_engine import indicators as ind
from algora_engine.backtest import BacktestConfig, run_backtest
from algora_engine.candles import Bar
from algora_engine.dsl import (
    SCHEMA_VERSION,
    Condition,
    ConstOperand,
    EntryRules,
    ExitConditions,
    Exits,
    IndicatorOperand,
    PrevSessionOperand,
    PriceOperand,
    SessionOperand,
    SideLevels,
    StrategySpec,
    TimeCondition,
    load_spec,
    upgrade,
)
from algora_engine.instruments import Segment
from algora_engine.rules import RuleSignals, Side
from algora_engine.templates import TEMPLATE_IDS, TemplateUnavailable, build_template, catalogue, params_model

CLOSE = PriceOperand(field="close")


def spec(inst, long=(), short=(), **kw):
    return StrategySpec(
        name="t", instrument=inst, timeframe="15m", entry=EntryRules(long=list(long), short=list(short)), **kw
    )


def test_v1_document_upgrades_unchanged():
    up = upgrade(VALID)
    assert up["schema_version"] == SCHEMA_VERSION == 2 and VALID["schema_version"] == 1  # input not mutated
    assert {k: v for k, v in up.items() if k != "schema_version"} == {
        k: v for k, v in VALID.items() if k != "schema_version"
    }
    s = load_spec(VALID)
    assert s.session.opening_range_minutes is None and s.session.signals_from == "after_opening_range"
    assert s.exits.stop_level is None and s.exits.conditions is None
    again = load_spec(s.model_dump(mode="json"))
    assert again == s


@pytest.mark.parametrize(
    ("build", "msg"),
    [
        (lambda: SessionOperand(ref="gap_pct", offset_pct=1), "takes no offset"),
        (lambda: IndicatorOperand(name="vwap", length=5), "vwap takes no length"),
        (lambda: IndicatorOperand(name="vwap", field="high"), "no field"),
        (lambda: IndicatorOperand(name="vwap", scope="continuous"), "session indicator"),
        (lambda: IndicatorOperand(name="ema"), "needs a length"),
        (lambda: TimeCondition(start="11:00", end="10:00"), "not be before"),
        (lambda: TimeCondition(start="9:00", end="10:00"), "HH:MM"),
        (lambda: SideLevels(long=SessionOperand(ref="range_width_pct")), "not a price level"),
        (lambda: SideLevels(), "long, short or both"),
        (lambda: ExitConditions(), "at least one"),
        (lambda: Exits(stop_pct=1, stop_level=SideLevels(long=SessionOperand(ref="range_low"))), "not both"),
        (lambda: Exits(target_pct=1, target_r_multiple=2, stop_pct=1), "only one of"),
        (lambda: Exits(target_r_multiple=2), "needs a stop"),
    ],
)
def test_v2_validation(build, msg):
    with pytest.raises(ValidationError, match=msg):
        build()


def test_vwap_defaults_to_session_scope_and_round_trips():
    op = IndicatorOperand(name="vwap")
    assert op.scope == "session" and op.key == ("vwap", None, "close", "session")
    doc = json.loads(json.dumps(VALID))
    doc["entry"]["long"].append({"op": "gt", "left": {"kind": "price"}, "right": {"kind": "indicator", "name": "vwap"}})
    doc["entry"]["long"].append({"op": "time_between", "start": "09:30", "end": "14:00"})
    s = load_spec(doc)
    assert s.uses_vwap() and isinstance(s.entry.long[2], TimeCondition)
    assert load_spec(s.model_dump(mode="json")) == s


def test_opening_range_validation(unit_future):
    with pytest.raises(ValidationError, match="multiple"):
        spec(
            unit_future,
            [Condition(op="gt", left=CLOSE, right=ConstOperand(value=1))],
            session={"opening_range_minutes": 20},
        )
    with pytest.raises(ValidationError, match="intraday"):
        StrategySpec.model_validate(
            {
                **load_spec(VALID).model_dump(mode="json"),
                "instrument": {"symbol": "INFY", "exchange": "NSE", "segment": "equity_delivery"},
            }
        )


def test_operands_and_flags(unit_future):
    gap = Condition(op="gt", left=SessionOperand(ref="gap_pct"), right=ConstOperand(value=0))
    s = spec(
        unit_future,
        [gap, TimeCondition(start="09:15", end="10:00")],
        exits=Exits(
            target_level=SideLevels(long=PrevSessionOperand()),
            conditions=ExitConditions(long=[Condition(op="lt", left=CLOSE, right=IndicatorOperand(name="vwap"))]),
        ),
    )
    assert s.uses_prev_session() and s.uses_vwap() and len(s.operands()) == 5
    assert not spec(unit_future, [Condition(op="gt", left=CLOSE, right=ConstOperand(value=0))]).uses_prev_session()


def test_indicators_by_hand():
    bars = make_bars("2026-06-03", "09:15", 5, [(1, 2, 0.5, c) for c in (10, 11, 12, 13)])
    sma = ind.SMA(2, "close")
    assert [sma.update(b) for b in bars] == [None, 10.5, 11.5, 12.5]
    ema = ind.EMA(2, "close")
    vals = [ema.update(b) for b in bars]
    assert vals[0] is None and vals[1] == 10.5 and vals[2] == pytest.approx(10.5 + 2 / 3 * 1.5)
    hi, lo = ind.Extreme(2, "close", True), ind.Extreme(2, "close", False)
    assert [hi.update(b) for b in bars] == [None, None, 11, 12]
    assert [lo.update(b) for b in bars] == [None, None, 10, 11]
    sma.reset()
    assert sma.update(bars[0]) is None
    v = ind.VWAP()
    assert v.update(Bar(bars[0].ts, 1, 3, 0, 3, 0)) is None  # no volume yet
    assert v.update(Bar(bars[1].ts, 1, 3, 0, 3, 10)) == pytest.approx(2)
    assert v.update(Bar(bars[2].ts, 1, 6, 0, 3, 10)) == pytest.approx(2.5)
    assert v.update(Bar(bars[3].ts, 1, 3, 0, 3, None)) is None  # a candle without volume ends it for the day
    assert v.update(Bar(bars[3].ts, 1, 3, 0, 3, 10)) is None
    v.reset()
    assert v.update(Bar(bars[0].ts, 1, 3, 0, 3, 10)) == pytest.approx(2)
    assert isinstance(ind.build(IndicatorOperand(name="sma", length=3)), ind.SMA)
    assert isinstance(ind.build(IndicatorOperand(name="lowest", length=3)), ind.Extreme)


def test_session_values_and_gap(unit_future):
    or_spec = spec(
        unit_future,
        [Condition(op="gt", left=CLOSE, right=SessionOperand(ref="range_high", offset_pct=1, offset_points=0.5))],
        session={"opening_range_minutes": 30},
    )
    sig = RuleSignals(or_spec)
    prev = make_bars("2026-06-02", "09:15", 15, [(98, 99, 97, 98.5)])
    for b in prev:
        sig.on_bar(b)
    day = make_bars("2026-06-03", "09:00", 15, [(50, 50, 50, 50), (100, 101, 99, 100), (100, 102, 99.5, 101)])
    sig.on_bar(day[0])  # before the session start: counted for the day, not for the levels
    assert sig.levels is None and sig.prev_session == {"open": 98, "high": 99, "low": 97, "close": 98.5}
    sig.on_bar(day[1])
    assert sig.range_width() is None and "range_high" not in sig.levels
    gap = SessionOperand(ref="gap_pct")
    assert sig.level(gap) == pytest.approx((100 - 98.5) / 98.5 * 100)
    assert sig.level(SessionOperand(ref="range_mid")) is None  # range still forming
    sig.on_bar(day[2])
    assert sig.levels["range_high"] == 102 and sig.levels["range_mid"] == 100.5 and sig.range_width() == 3
    assert sig.level(SessionOperand(ref="range_width_pct")) == pytest.approx(3 / 101 * 100)
    assert sig.level(SessionOperand(ref="range_high", offset_pct=1, offset_points=0.5)) == pytest.approx(103.52)
    assert sig.level(PrevSessionOperand(field="high", offset_points=-1)) == 98


def test_missing_range_candle_completes_the_range_on_the_next_one(unit_future):
    c = Condition(op="gt", left=CLOSE, right=SessionOperand(ref="range_high"))
    sig = RuleSignals(spec(unit_future, [c], session={"opening_range_minutes": 30}))
    bars = make_bars("2026-06-03", "09:15", 15, [(100, 101, 99, 100)])
    bars += make_bars("2026-06-03", "09:45", 15, [(100, 103, 100, 102)])  # the 09:30 candle is missing
    assert sig.on_bar(bars[0]) is None
    assert sig.on_bar(bars[1]) is Side.LONG  # range = 09:15 only (high 101); 09:45 is evaluated
    assert sig.levels["range_high"] == 101


def test_unavailable_operands_make_conditions_false(unit_future):
    no_prev = Condition(op="gt", left=PrevSessionOperand(), right=ConstOperand(value=0))
    sig = RuleSignals(spec(unit_future, [no_prev], session={"signals_from": "session_start"}))
    b = make_bars("2026-06-03", "09:15", 15, [(100, 101, 99, 100)] * 2)
    assert sig.on_bar(b[0]) is None and sig.on_bar(b[1]) is None
    assert sig.prev_session_status == {b[0].day: "none"}
    cross = Condition(op="crosses_above", left=CLOSE, right=IndicatorOperand(name="sma", length=2))
    sig = RuleSignals(spec(unit_future, [cross], session={"signals_from": "session_start"}))
    assert sig.on_bar(b[0]) is None  # no previous candle and no SMA yet
    assert sig.on_bar(b[1]) is None  # SMA now 100, but it was unavailable on the previous candle
    gap = Condition(op="gt", left=SessionOperand(ref="gap_pct"), right=ConstOperand(value=0))
    sig = RuleSignals(spec(unit_future, [gap]))
    assert sig.level(SessionOperand(ref="gap_pct")) is None


def test_exit_conditions_close_at_next_open(unit_future):
    entry = Condition(op="gt", left=CLOSE, right=ConstOperand(value=100))
    leave = Condition(op="lt", left=CLOSE, right=ConstOperand(value=101))
    s = spec(
        unit_future,
        [entry],
        exits=Exits(flip=False, conditions=ExitConditions(long=[leave])),
        session={"square_off": "10:30"},
    )
    rows = [
        (100, 101, 99, 100),
        (100, 102, 99.5, 101.5),
        (101.6, 102, 101.2, 101.8),
        (101.8, 102, 100.5, 100.8),
        (100.7, 101, 100, 100.5),
        (100.4, 100.6, 100, 100.2),
    ]
    r = run_backtest(s, make_bars("2026-06-03", "09:15", 15, rows), BacktestConfig(apply_charges=False))
    # Long signal 09:30 close (101.5) -> fill 09:45 at 101.6; 10:00 close 100.8 < 101 -> exit 10:15 open 100.7.
    t = r.trades[0]
    assert (t.entry_price, t.exit_reason, t.exit_price) == (101.6, "Exit rule", 100.7)
    assert len(r.trades) == 1  # 10:15 closes 100.5 > 100 again, but max_entries_per_day is 1


def test_level_stop_unavailable_leaves_no_stop(unit_future):
    # A previous-session stop level with no previous session: the trade simply has no stop (square-off exit).
    entry = Condition(op="gt", left=CLOSE, right=ConstOperand(value=100))
    s = spec(
        unit_future,
        [entry],
        exits=Exits(stop_level=SideLevels(long=PrevSessionOperand(field="low")), target_r_multiple=2),
        session={"square_off": "10:00"},
    )
    rows = [(100, 101, 99, 100), (100, 102, 99.5, 101.5), (101.6, 102, 90, 101.8), (101.8, 102, 100.5, 100.8)]
    t = run_backtest(s, make_bars("2026-06-03", "09:15", 15, rows), BacktestConfig(apply_charges=False)).trades[0]
    assert (t.exit_reason, t.exit_price) == ("Square-off", 101.8)
    # Only a short level given: a long trade has no stop either.
    s2 = spec(
        unit_future,
        [entry],
        exits=Exits(stop_level=SideLevels(short=SessionOperand(ref="range_high"))),
        session={"square_off": "10:00"},
    )
    t = run_backtest(s2, make_bars("2026-06-03", "09:15", 15, rows), BacktestConfig(apply_charges=False)).trades[0]
    assert t.exit_reason == "Square-off"


def test_target_range_multiple_needs_a_range_width(unit_future):
    # A flat first candle (high == low) has no width: no range-multiple target is set.
    entry = Condition(op="gt", left=CLOSE, right=ConstOperand(value=100))
    s = spec(unit_future, [entry], exits=Exits(target_range_multiple=1), session={"square_off": "10:00"})
    rows = [(100, 100, 100, 100), (100, 102, 99.5, 101.5), (101.6, 120, 101, 101.8), (101.8, 102, 100.5, 100.8)]
    t = run_backtest(s, make_bars("2026-06-03", "09:15", 15, rows), BacktestConfig(apply_charges=False)).trades[0]
    assert t.exit_reason == "Square-off"


def test_calendar_session_helpers():
    from datetime import date

    assert cal.is_session_day(date(2026, 6, 26), Segment.COMMODITY_FUTURES)  # evening session
    assert not cal.is_session_day(date(2026, 6, 26), Segment.FUTURES)
    assert not cal.is_session_day(date(2026, 10, 2), Segment.COMMODITY_OPTIONS)
    f = cal.previous_session_check
    assert f(date(2026, 6, 25), date(2026, 6, 29), Segment.FUTURES) == "ok"
    assert f(date(2026, 6, 25), date(2026, 6, 29), Segment.COMMODITY_FUTURES) == "missing"  # MCX open on the 26th
    assert f(date(2026, 6, 1), date(2026, 6, 3), Segment.EQUITY_INTRADAY) == "missing"
    assert f(date(2026, 12, 31), date(2027, 1, 4), Segment.FUTURES) == "unverified"  # 1 Jan 2027: no list yet
    assert f(date(2027, 1, 8), date(2027, 1, 11), Segment.FUTURES) == "ok"  # only a weekend between


def test_catalogue_lists_every_template():
    cat = {t["id"]: t for t in catalogue()}
    assert set(cat) == {*TEMPLATE_IDS, "monthly_option_spread"}
    for tid in TEMPLATE_IDS:
        t = cat[tid]
        assert t["available"] and t["parameters"]["type"] == "object" and t["defaults"]
        assert set(t["defaults"]) == set(t["parameters"]["properties"])
        build_template(tid, name="x", instrument=_inst(), params=t["defaults"])  # defaults always build
    assert cat["opening_range_breakout"]["defaults"]["opening_range_minutes"] == 30
    ms = cat["monthly_option_spread"]
    assert not ms["available"] and "option-chain" in ms["reason"] and ms["parameters"] is None
    with pytest.raises(TemplateUnavailable):
        params_model("monthly_option_spread")
    with pytest.raises(KeyError):
        params_model("martingale")


def test_first_candle_cross_through_the_catalogue():
    s = build_template("first_candle_cross", name="x", instrument=_inst(), params={"buffer_pct": 0.1, "stop_pct": 1})
    assert s.template_params["buffer_pct"] == 0.1 and s.template_params["start"] == "09:15"
    assert s.exits.stop_pct == 1 and s.entry.long[0].right.offset_pct == 0.1


def _inst():
    from algora_engine.dsl import Instrument

    return Instrument(symbol="NIFTY", exchange="NFO", segment="futures")
