import json

import pytest
from conftest import audit_actions
from test_resources import SPEC

# 15m NIFTY candles (Wed 3 Jun 2026). Long signal at 09:45, fills 104 at 10:00, square-off 10:30 at 106.
CSV = """datetime,open,high,low,close
2026-06-03 09:15,100,102,99,101
2026-06-03 09:30,101,102.5,100.5,102
2026-06-03 09:45,102,104,101.5,103.5
2026-06-03 10:00,104,105,103,104.5
2026-06-03 10:15,104.5,106,104,105.5
2026-06-03 10:30,106,107,105,106.5
"""
FLIP_ONLY = {**SPEC, "exits": {"flip": True}}


def post_bt(client, h, *, spec=FLIP_ONLY, csv=CSV, config=None, strategy_id=None, name="nifty.csv"):
    data = {}
    if spec is not None:
        data["spec"] = spec if isinstance(spec, str) else json.dumps(spec)
    if strategy_id is not None:
        data["strategy_id"] = strategy_id
    if config is not None:
        data["config"] = config if isinstance(config, str) else json.dumps(config)
    content = csv.encode() if isinstance(csv, str) else csv
    return client.post("/backtests", data=data, files={"file": (name, content, "text/csv")}, headers=h)


def test_backtest_inline_spec_matches_hand_computed(user, client):
    r = post_bt(client, user.headers)
    assert r.status_code == 201, r.text
    run = r.json()
    t = run["trades"][0]
    assert (t["entry_time"], t["entry_price"], t["exit_time"], t["exit_price"]) == (
        "2026-06-03T10:00",
        104.0,
        "2026-06-03T10:30",
        106.0,
    )
    # 1 lot x 65 (NSE/FAOP/70616) x 2 points = 130 gross; charges 8.73 (see engine test_charges_are_deducted_per_trade)
    assert t["qty"] == 65 and t["gross_pnl"] == 130.0 and t["charges"]["total"] == pytest.approx(8.73)
    assert run["summary"]["net_pnl"] == pytest.approx(121.27)
    assert run["candles_meta"]["rows"] == 6 and run["candles_meta"]["timeframe_minutes"] == 15
    assert run["candles_meta"]["file_name"] == "nifty.csv" and len(run["candles_meta"]["sha256"]) == 64
    assert run["strategy_id"] is None and run["engine_version"] == "0.2.0"
    assert run["equity_curve"][-1]["equity"] == pytest.approx(121.27)
    assert "backtest.create" in audit_actions(user.email)


def test_backtest_saved_strategy_and_listing(user, client):
    h = user.headers
    s = client.post("/strategies", json={"spec": FLIP_ONLY}, headers=h).json()
    r = post_bt(client, h, spec=None, strategy_id=s["id"], config={"apply_charges": False, "slippage_points": 0.5})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["strategy_id"] == s["id"] and run["strategy_version"] == 1
    assert run["summary"]["net_pnl"] == pytest.approx(65.0)  # (105.5 - 104.5) x 65
    post_bt(client, h)
    page = client.get("/backtests", headers=h).json()
    assert page["total"] == 2 and "trades" not in page["items"][0]
    assert client.get(f"/backtests?strategy_id={s['id']}", headers=h).json()["total"] == 1
    full = client.get(f"/backtests/{run['id']}", headers=h).json()
    assert full["config"]["slippage_points"] == 0.5 and len(full["trades"]) == 1
    assert client.delete(f"/backtests/{run['id']}", headers=h).status_code == 204
    assert client.get(f"/backtests/{run['id']}", headers=h).status_code == 404
    # Deleting the strategy keeps its runs (strategy_id set to NULL).
    client.delete(f"/strategies/{s['id']}", headers=h)
    assert client.get("/backtests", headers=h).json()["total"] == 1


@pytest.mark.parametrize(
    ("kwargs", "status", "msg"),
    [
        ({"spec": None}, 422, "exactly one"),
        ({"spec": "{nope"}, 422, "valid JSON"),
        ({"spec": "[1]"}, 422, "JSON object"),
        ({"config": {"slippage_pct": 9}}, 422, "less than or equal"),
        ({"config": "{x"}, 422, "valid JSON"),
        ({"csv": "datetime,open\n1,2\n"}, 422, "Missing columns"),
        ({"csv": b"\xff\xfe\x00bad"}, 422, "UTF-8"),
        ({"csv": ""}, 422, "empty"),
        ({"csv": "date,open,high,low,close\n2026-06-01,1,2,0.5,1\n2026-06-02,1,2,0.5,1\n"}, 422, "intraday"),
        (
            {"spec": {**FLIP_ONLY, "instrument": {"symbol": "TCS", "exchange": "NFO", "segment": "futures"}}},
            422,
            "lot size",
        ),
    ],
)
def test_backtest_errors(user, client, kwargs, status, msg):
    r = post_bt(client, user.headers, **kwargs)
    assert r.status_code == status, r.text
    assert msg in json.dumps(r.json())


def test_backtest_file_size_limit(user, client, app):
    from app.config import get_settings

    settings = get_settings()
    old = settings.max_upload_bytes
    settings.max_upload_bytes = 1024
    try:
        r = post_bt(client, user.headers, csv=CSV + "2026-06-04 09:15,1,2,0.5,1\n" * 60)
        assert r.status_code == 413
    finally:
        settings.max_upload_bytes = old


def test_backtests_are_private(make_user, client):
    alice, bob = make_user("alice@example.com"), make_user("bob@example.com")
    run = post_bt(client, alice.headers).json()
    assert client.get(f"/backtests/{run['id']}", headers=bob.headers).status_code == 404
    assert client.delete(f"/backtests/{run['id']}", headers=bob.headers).status_code == 404
    s = client.post("/strategies", json={"spec": FLIP_ONLY}, headers=alice.headers).json()
    assert post_bt(client, bob.headers, spec=None, strategy_id=s["id"]).status_code == 404


# ----- import ---------------------------------------------------------------------------------------------------

EXPORT = {
    "app": "algora",
    "version": 1,
    "exportedAt": "2026-11-20T10:00:00.000Z",
    "theme": "dark",
    "accounts": {"zerodha": ["Main", "Swing"], "dhan": ["Wife"], "fakebroker": ["x"]},
    "bots": [
        {
            "name": "Nifty FCB",
            "strategy": "First Candle Cross",
            "inst": "NIFTY",
            "acctLabel": "Zerodha · Main",
            "tf": "15m",
            "buf": 0.1,
            "exit": "target",
            "exitv": 1.5,
            "sq": "15:15",
            "max": 2,
            "qty": 1,
            "paused": False,
        },
        {
            "name": "Crude FCB",
            "strategy": "First Candle Cross",
            "inst": "CRUDEOIL",
            "acctLabel": "Upstox · New",
            "tf": "5m",
            "buf": 0,
            "exit": "stop",
            "exitv": 0.5,
            "sq": "23:15",
            "max": 1,
            "qty": 2,
            "paused": True,
        },
        {
            "name": "ORB",
            "strategy": "Opening Range Breakout",
            "inst": "BANKNIFTY",
            "acctLabel": "Zerodha · Main",
            "tf": "15m",
            "buf": 0,
            "exit": "flip",
            "exitv": 0,
            "sq": "15:15",
            "max": 1,
            "qty": 1,
            "paused": False,
        },
        {
            "name": "Broken",
            "strategy": "First Candle Cross",
            "inst": "NIFTY",
            "acctLabel": "Zerodha · Main",
            "tf": "15m",
            "buf": 99,
            "exit": "flip",
            "exitv": 0,
            "sq": "15:15",
            "max": 1,
            "qty": 1,
        },
        "garbage",
    ],
    "backtest": {
        "strategy": "First Candle Cross",
        "start": "09:15",
        "sq": "15:15",
        "buf": "0.1",
        "exitv": "1",
        "max": "2",
        "lot": "65",
        "delta": "0.5",
        "cost": "40",
        "exit": "target",
        "evil": "<script>",
    },
    "risk": {"loss": 5000, "trades": 10, "cut": "14:30"},
}


def test_import_browser_export(user, client):
    h = user.headers
    r = client.post("/import", json=EXPORT, headers=h)
    assert r.status_code == 200, r.text
    res = r.json()
    # Main, Swing, Wife from the account list, plus "Upstox · New" referenced by a bot
    assert res["accounts_created"] == 4 and res["strategies_created"] == 3
    reasons = {s["name"]: s["reason"] for s in res["strategies_skipped"]}
    assert "buffer" in reasons["Broken"] and reasons[""] == "not an object" and "ORB" not in reasons
    details = {d["name"]: d for d in res["strategies_created_details"]}
    assert details["ORB"]["template"] == "opening_range_breakout" and details["ORB"]["browser_strategy"] == (
        "Opening Range Breakout"
    )
    assert details["ORB"]["defaults_used"] == {"opening_range_minutes": 30, "buffer_type": "pct", "direction": "both"}
    assert details["Nifty FCB"]["defaults_used"] == {} and details["Nifty FCB"]["template"] == "first_candle_cross"
    assert res["risk_imported"] and res["backtest_settings_imported"]

    strategies = client.get("/strategies", headers=h).json()["items"]
    nifty = next(s for s in strategies if s["name"] == "Nifty FCB")
    crude = next(s for s in strategies if s["name"] == "Crude FCB")
    assert nifty["spec"]["exits"]["target_pct"] == 1.5 and nifty["spec"]["risk"]["max_entries_per_day"] == 2
    assert crude["paused"] is True and crude["spec"]["instrument"]["exchange"] == "MCX"
    accounts = {(a["broker_id"], a["label"]): a["id"] for a in client.get("/accounts", headers=h).json()}
    assert nifty["broker_account_id"] == accounts[("zerodha", "Main")]
    assert crude["broker_account_id"] == accounts[("upstox", "New")]
    risk = client.get("/risk", headers=h).json()
    assert risk["daily_loss_limit"] == "5000.00" and risk["max_trades_per_day"] == 10
    assert "import.browser" in audit_actions(user.email)

    # Re-importing the same file creates nothing new.
    again = client.post("/import", json=EXPORT, headers=h).json()
    assert again["accounts_created"] == 0 and again["strategies_created"] == 0
    assert {s["reason"] for s in again["strategies_skipped"] if s["name"] in ("Nifty FCB", "Crude FCB", "ORB")} == {
        "already imported"
    }
    assert again["risk_imported"] is False  # limits already on the server are never overwritten


def test_import_backtest_settings_are_cleaned(user, client):
    from sqlalchemy import text

    from app.db import get_engine

    client.post("/import", json=EXPORT, headers=user.headers)
    with get_engine().connect() as conn:
        settings = conn.execute(text("SELECT settings FROM users")).scalar_one()
    bt = settings["browser_backtest_defaults"]
    assert "evil" not in bt and bt["lot"] == "65" and bt["buf"] == "0.1" and bt["exit"] == "target"


def test_import_rejects_other_files(user, client):
    assert client.post("/import", json={"app": "other", "version": 1}, headers=user.headers).status_code == 422
    assert client.post("/import", json={"app": "algora", "version": 2}, headers=user.headers).status_code == 422
    r = client.post("/import", json={"app": "algora", "version": 1}, headers=user.headers)
    assert r.status_code == 200 and r.json()["strategies_created"] == 0


def test_import_duplicate_bots_in_one_file(user, client):
    bot = EXPORT["bots"][0]
    r = client.post("/import", json={"app": "algora", "version": 1, "bots": [bot, bot]}, headers=user.headers).json()
    assert r["strategies_created"] == 1 and r["strategies_skipped"] == [
        {"name": "Nifty FCB", "reason": "already imported"}
    ]


def test_import_body_limit(user, client):
    big = {"app": "algora", "version": 1, "bots": [], "pad": "x" * (1024 * 1024 + 10)}
    assert client.post("/import", json=big, headers=user.headers).status_code == 413
