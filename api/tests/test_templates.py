"""Template catalogue, template -> spec, backtests and imports of the Phase 2 templates, schema v2 migration."""

import json
import os

import pytest
from alembic import command
from alembic.config import Config
from conftest import API_DIR, TEST_DB
from sqlalchemy import text
from test_backtests_import import post_bt

from app.db import get_engine

NIFTY_UNIT = {"symbol": "NIFTY", "exchange": "NFO", "segment": "futures", "lot_size": 1}
NO_COSTS = {"apply_charges": False}

# Wed 3 Jun 2026, 5m. ORB (15-min range 09:15-09:30): high 102, low 99. Close 102.6 at 09:35 crosses above 102 ->
# long fills 102.8 at 09:40; target 102.8 + 1 x range width (3) = 105.8, reached at 09:50 -> +3.00 points.
ORB_CSV = """datetime,open,high,low,close,volume
2026-06-03 09:15,100,101,99,100.5,1000
2026-06-03 09:20,100.5,101.5,100,101,1000
2026-06-03 09:25,101,102,100.5,101.5,1000
2026-06-03 09:30,101.5,102,101,101.8,1000
2026-06-03 09:35,101.8,103,101.6,102.6,1000
2026-06-03 09:40,102.8,103.5,102.5,103.2,1000
2026-06-03 09:45,103.2,104,103,103.8,1000
2026-06-03 09:50,103.8,106,103.5,105.5,1000
2026-06-03 09:55,105.5,105.6,105,105.2,1000
2026-06-03 10:00,105.2,105.4,105,105.1,1000
"""
# Gap fade: previous close (2 Jun, 15:25 candle) 100; 3 Jun opens 101.2 (+1.2%), first candle closes 101.3 > 100:
# short fills 101.3 at 09:20; target = previous close 100, reached at 09:25 -> +1.30 points.
GAP_CSV = """datetime,open,high,low,close
2026-06-02 09:15,99,100.5,98.5,99.5
2026-06-02 15:25,99.6,100.2,99.4,100
2026-06-03 09:15,101.2,101.5,100.9,101.3
2026-06-03 09:20,101.3,101.4,100.6,100.8
2026-06-03 09:25,100.8,100.9,99.9,100.1
2026-06-03 09:30,100.1,100.3,99.8,100
"""
# Momentum, EMA 2/3 crossover (see engine tests/test_momentum.py): long 101.2 at 09:40, flip at 09:55 (99.9,
# -1.3), short squared off at 10:00 (98.2, +1.7).
MOM_CSV = """datetime,open,high,low,close
2026-06-03 09:15,100,100.5,99.5,100
2026-06-03 09:20,100,100.2,98.8,99
2026-06-03 09:25,99,99.2,97.8,98
2026-06-03 09:30,98,99.3,97.9,99
2026-06-03 09:35,99,101.2,98.9,101
2026-06-03 09:40,101.2,103.3,101,103
2026-06-03 09:45,103,103.4,101.8,102
2026-06-03 09:50,102,102.2,99.8,100
2026-06-03 09:55,99.9,100,97.8,98
2026-06-03 10:00,98.2,98.5,98,98.3
"""


def build(client, template, params=None, instrument=NIFTY_UNIT, name="T"):
    return client.post(
        f"/templates/{template}/spec", json={"name": name, "instrument": instrument, "params": params or {}}
    )


def test_catalogue(client):
    r = client.get("/templates")
    assert r.status_code == 200
    items = {t["id"]: t for t in r.json()["templates"]}
    assert list(items) == [
        "first_candle_cross",
        "opening_range_breakout",
        "gap_fade",
        "momentum",
        "monthly_option_spread",
    ]
    orb = items["opening_range_breakout"]
    assert orb["name"] == "Opening Range Breakout" and orb["available"] is True
    assert orb["defaults"]["opening_range_minutes"] == 30 and orb["defaults"]["stop_type"] == "range_opposite"
    assert "opening_range_minutes" in orb["parameters"]["properties"]
    assert items["gap_fade"]["defaults"]["target_type"] == "prev_close"
    assert items["momentum"]["defaults"]["fast_length"] == 9 and items["momentum"]["defaults"]["slow_length"] == 21
    ms = items["monthly_option_spread"]
    assert ms["available"] is False and "option-chain" in ms["reason"] and ms["defaults"] is None


def test_build_spec_with_defaults(client):
    r = build(client, "opening_range_breakout")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["template"] == "opening_range_breakout" and out["spec"]["schema_version"] == 2
    assert out["spec"]["session"]["opening_range_minutes"] == 30
    assert out["params_used"]["start"] == "09:15" and out["params_used"]["square_off"] == "15:15"
    assert "opening_range_minutes" in out["defaults_used"]
    mcx = build(
        client,
        "gap_fade",
        {"min_gap_pct": 1},
        {"symbol": "CRUDEOIL", "exchange": "MCX", "segment": "commodity_futures"},
    ).json()
    assert mcx["spec"]["session"]["start"] == "09:00" and mcx["params_used"]["entry_end"] == "09:45"
    assert "min_gap_pct" not in mcx["defaults_used"]


@pytest.mark.parametrize(
    ("template", "params", "instrument", "status", "msg"),
    [
        ("martingale", {}, NIFTY_UNIT, 404, "Unknown template"),
        ("monthly_option_spread", {}, NIFTY_UNIT, 422, "option-chain"),
        ("opening_range_breakout", {"buffer": 9}, NIFTY_UNIT, 422, "0 to 5"),
        ("momentum", {"fast_length": 30}, NIFTY_UNIT, 422, "shorter"),
        ("gap_fade", {"bogus": 1}, NIFTY_UNIT, 422, "Extra inputs"),
        ("opening_range_breakout", {}, {"symbol": "NIFTY", "exchange": "MCX", "segment": "futures"}, 422, "trades on"),
        ("opening_range_breakout", {"timeframe": "15m", "opening_range_minutes": 20}, NIFTY_UNIT, 422, "multiple"),
    ],
)
def test_build_spec_errors(client, template, params, instrument, status, msg):
    r = build(client, template, params, instrument)
    assert r.status_code == status
    assert msg in json.dumps(r.json())


def test_orb_backtest_from_template_matches_hand_computed(user, client):
    spec = build(client, "opening_range_breakout", {"opening_range_minutes": 15, "square_off": "10:00"}).json()["spec"]
    r = post_bt(client, user.headers, spec=spec, csv=ORB_CSV, config=NO_COSTS)
    assert r.status_code == 201, r.text
    t = r.json()["trades"]
    assert [(x["side"], x["entry_time"], x["entry_price"], x["exit_time"], x["reason"], x["points"]) for x in t] == [
        ("Long", "2026-06-03T09:40", 102.8, "2026-06-03T09:50", "Target", 3.0)
    ]
    assert r.json()["spec"]["template"] == "opening_range_breakout"


def test_gap_fade_saved_strategy_backtest(user, client):
    h = user.headers
    spec = build(client, "gap_fade", name="Fade").json()["spec"]
    s = client.post("/strategies", json={"spec": spec}, headers=h)
    assert s.status_code == 201, s.text
    assert s.json()["template"] == "gap_fade"
    r = post_bt(client, h, spec=None, strategy_id=s.json()["id"], csv=GAP_CSV, config=NO_COSTS)
    assert r.status_code == 201, r.text
    run = r.json()
    t = run["trades"][0]
    assert (t["side"], t["entry_price"], t["reason"], t["exit_price"], t["points"]) == (
        "Short",
        101.3,
        "Target",
        100,
        1.3,
    )
    assert any("no previous session" in w for w in run["warnings"])


def test_momentum_backtest_and_vwap_needs_volume(user, client):
    p = {"fast_length": 2, "slow_length": 3, "stop_type": "none", "square_off": "10:00"}
    spec = build(client, "momentum", p).json()["spec"]
    run = post_bt(client, user.headers, spec=spec, csv=MOM_CSV, config=NO_COSTS).json()
    assert [(t["side"], t["reason"], t["points"]) for t in run["trades"]] == [
        ("Long", "Flip", -1.3),
        ("Short", "Square-off", 1.7),
    ]
    spec = build(client, "momentum", {**p, "vwap_filter": True}).json()["spec"]
    r = post_bt(client, user.headers, spec=spec, csv=MOM_CSV, config=NO_COSTS)
    assert r.status_code == 422 and "volume" in r.json()["detail"]


def test_holiday_session_is_not_traded(user, client):
    # Same ORB candles moved to Fri 26 Jun 2026 (NSE holiday): no trades, and a warning says why.
    spec = build(client, "opening_range_breakout", {"opening_range_minutes": 15, "square_off": "10:00"}).json()["spec"]
    run = post_bt(client, user.headers, spec=spec, csv=ORB_CSV.replace("2026-06-03", "2026-06-26"), config=NO_COSTS)
    assert run.json()["trades"] == [] and any("No trades were taken" in w for w in run.json()["warnings"])


IMPORT = {
    "app": "algora",
    "version": 1,
    "bots": [
        {
            "name": n,
            "strategy": s,
            "inst": "NIFTY",
            "tf": "5m",
            "buf": 0.1,
            "exit": e,
            "exitv": v,
            "sq": "15:15",
            "max": 1,
            "qty": 1,
        }
        for n, s, e, v in (
            ("Gap", "Gap fade", "flip", 0),
            ("Mom", "Momentum", "stop", 0.5),
            ("Spread", "Monthly option spread", "flip", 0),
        )
    ],
    "backtest": {"strategy": "Gap fade", "start": "09:15"},
}


def test_import_maps_gap_fade_and_momentum_and_reports_defaults(user, client):
    res = client.post("/import", json=IMPORT, headers=user.headers).json()
    assert res["strategies_created"] == 2
    d = {x["name"]: x for x in res["strategies_created_details"]}
    assert d["Gap"]["template"] == "gap_fade" and d["Gap"]["defaults_used"]["target_type"] == "prev_close"
    assert any("buffer ignored" in n for n in d["Gap"]["notes"])
    assert d["Mom"]["template"] == "momentum" and d["Mom"]["defaults_used"]["fast_length"] == 9
    assert any("positional rotation" in n for n in d["Mom"]["notes"])
    assert res["strategies_skipped"] == [
        {
            "name": "Spread",
            "reason": "Monthly option spread is not available in the engine yet: it needs option-chain "
            "data the engine does not have yet",
        }
    ]
    saved = {s["name"]: s for s in client.get("/strategies", headers=user.headers).json()["items"]}
    assert saved["Mom"]["spec"]["exits"]["stop_pct"] == 0.5 and saved["Gap"]["template"] == "gap_fade"
    with get_engine().connect() as conn:
        bt = conn.execute(text("SELECT settings FROM users")).scalar_one()["browser_backtest_defaults"]
    assert bt["strategy"] == "Gap fade"


# ----- migration 0002 ---------------------------------------------------------------------------------------------


def _alembic() -> Config:
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.attributes["database_url"] = TEST_DB
    cfg.attributes["skip_logging"] = True
    return cfg


def _rows():
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT schema_version, spec FROM strategy_versions ORDER BY created_at")).all()


def test_migration_0002_round_trip_and_refuses_lossy_downgrade(user, client):
    from algora_engine.dsl import load_spec
    from test_resources import SPEC

    h = user.headers
    assert client.post("/strategies", json={"spec": SPEC}, headers=h).status_code == 201  # a v1 FCB document
    cfg = _alembic()
    try:
        command.downgrade(cfg, "0001")
        (row,) = _rows()
        assert row.schema_version == 1 and row.spec["schema_version"] == 1
        assert "opening_range_minutes" not in row.spec["session"] and "offset_points" not in json.dumps(row.spec)
        command.upgrade(cfg, "head")
        (row,) = _rows()
        assert row.schema_version == 2 and row.spec["schema_version"] == 2
        assert load_spec(row.spec).template == SPEC.get("template", "custom")
        # A strategy using v2 features cannot go back to v1: the downgrade refuses and changes nothing.
        orb = build(client, "opening_range_breakout").json()["spec"]
        assert client.post("/strategies", json={"spec": orb}, headers=h).status_code == 201
        with pytest.raises(RuntimeError, match="schema v2 features"):
            command.downgrade(cfg, "0001")
        assert {r.schema_version for r in _rows()} == {2}
    finally:
        command.upgrade(cfg, "head")
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002"
