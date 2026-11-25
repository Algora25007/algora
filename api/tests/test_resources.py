"""Broker accounts, strategies (versioned) and risk limits."""

import copy

import pytest
from conftest import audit_actions
from sqlalchemy import text

from app.db import get_engine

SPEC = {
    "schema_version": 1,
    "name": "Nifty FCB",
    "template": "first_candle_cross",
    "instrument": {"symbol": "NIFTY", "exchange": "NFO", "segment": "futures"},
    "timeframe": "15m",
    "session": {"start": "09:15", "square_off": "10:30"},
    "entry": {
        "long": [
            {
                "op": "crosses_above",
                "left": {"kind": "price", "field": "close"},
                "right": {"kind": "session", "ref": "first_candle_high"},
            }
        ],
        "short": [
            {
                "op": "crosses_below",
                "left": {"kind": "price", "field": "close"},
                "right": {"kind": "session", "ref": "first_candle_low"},
            }
        ],
    },
    "exits": {"stop_pct": 1, "target_pct": 2},
    "risk": {"max_entries_per_day": 1, "quantity_lots": 1},
}


# ----- accounts -------------------------------------------------------------------------------------------------


def test_account_crud_and_audit(user, client):
    h = user.headers
    r = client.post("/accounts", json={"broker_id": "zerodha", "label": "  Main "}, headers=h)
    assert r.status_code == 201
    acct = r.json()
    assert acct["label"] == "Main" and acct["broker_id"] == "zerodha"
    assert client.post("/accounts", json={"broker_id": "zerodha", "label": "Main"}, headers=h).status_code == 409
    assert client.post("/accounts", json={"broker_id": "dhan", "label": "Main"}, headers=h).status_code == 201
    assert [a["broker_id"] for a in client.get("/accounts", headers=h).json()] == ["zerodha", "dhan"]
    r = client.patch(f"/accounts/{acct['id']}", json={"label": "Swing"}, headers=h)
    assert r.status_code == 200 and r.json()["label"] == "Swing"
    assert client.delete(f"/accounts/{acct['id']}", headers=h).status_code == 204
    assert client.delete(f"/accounts/{acct['id']}", headers=h).status_code == 404
    acts = audit_actions(user.email)
    assert acts.count("account.create") == 2 and "account.update" in acts and "account.delete" in acts
    with get_engine().connect() as conn:
        before, after = conn.execute(text("SELECT before, after FROM audit_log WHERE action = 'account.update'")).one()
    assert before == {"broker_id": "zerodha", "label": "Main"} and after["label"] == "Swing"


def test_account_rename_conflict(user, client):
    h = user.headers
    a = client.post("/accounts", json={"broker_id": "fyers", "label": "A"}, headers=h).json()
    client.post("/accounts", json={"broker_id": "fyers", "label": "B"}, headers=h)
    assert client.patch(f"/accounts/{a['id']}", json={"label": "B"}, headers=h).status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"broker_id": "robinhood", "label": "x"},
        {"broker_id": "zerodha", "label": "   "},
        {"broker_id": "zerodha", "label": "x" * 41},
        {"broker_id": "zerodha", "label": "bad\nlabel"},
        {"broker_id": "zerodha", "label": "ok", "api_secret": "nope"},
    ],
)
def test_account_validation(user, client, body):
    assert client.post("/accounts", json=body, headers=user.headers).status_code == 422


def test_accounts_are_private(make_user, client):
    alice, bob = make_user("alice@example.com"), make_user("bob@example.com")
    a = client.post("/accounts", json={"broker_id": "upstox", "label": "Main"}, headers=alice.headers).json()
    assert client.get("/accounts", headers=bob.headers).json() == []
    assert client.patch(f"/accounts/{a['id']}", json={"label": "x"}, headers=bob.headers).status_code == 404
    assert client.delete(f"/accounts/{a['id']}", headers=bob.headers).status_code == 404


def test_requires_auth(client):
    for method, path in [
        ("get", "/accounts"),
        ("get", "/strategies"),
        ("get", "/risk"),
        ("get", "/backtests"),
        ("post", "/import"),
    ]:
        assert getattr(client, method)(path).status_code == 401


# ----- strategies -----------------------------------------------------------------------------------------------


def test_strategy_lifecycle(user, client):
    h = user.headers
    acct = client.post("/accounts", json={"broker_id": "zerodha", "label": "Main"}, headers=h).json()
    r = client.post("/strategies", json={"spec": SPEC, "broker_account_id": acct["id"]}, headers=h)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["current_version"] == 1 and s["template"] == "first_candle_cross"
    assert s["spec"]["exits"]["stop_pct"] == 1 and s["spec"]["exits"]["target_pct"] == 2
    assert s["spec"]["exits"]["flip"] is True  # defaults are written out
    sid = s["id"]

    v2 = copy.deepcopy(SPEC)
    v2["exits"]["trailing_stop_pct"] = 0.5
    v2["name"] = "Nifty FCB trail"
    r = client.put(f"/strategies/{sid}", json={"spec": v2}, headers=h)
    assert r.status_code == 200 and r.json()["current_version"] == 2 and r.json()["name"] == "Nifty FCB trail"
    # Same rules again: no new version.
    assert client.put(f"/strategies/{sid}", json={"spec": v2}, headers=h).json()["current_version"] == 2
    versions = client.get(f"/strategies/{sid}/versions", headers=h).json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["spec"]["exits"].get("trailing_stop_pct") is None

    r = client.patch(f"/strategies/{sid}", json={"paused": True, "clear_broker_account": True}, headers=h)
    assert r.json()["paused"] is True and r.json()["broker_account_id"] is None and r.json()["current_version"] == 2
    r = client.patch(f"/strategies/{sid}", json={"broker_account_id": acct["id"]}, headers=h)
    assert r.json()["broker_account_id"] == acct["id"]
    assert client.get(f"/strategies/{sid}", headers=h).json()["spec"]["name"] == "Nifty FCB trail"

    page = client.get("/strategies?limit=1", headers=h).json()
    assert page["total"] == 1 and len(page["items"]) == 1 and page["limit"] == 1

    assert client.delete(f"/strategies/{sid}", headers=h).status_code == 204
    assert client.get(f"/strategies/{sid}", headers=h).status_code == 404
    acts = audit_actions(user.email)
    assert acts.count("strategy.create") == 1 and acts.count("strategy.update") == 3 and "strategy.delete" in acts


def test_strategy_validation_errors(user, client):
    h = user.headers
    bad = copy.deepcopy(SPEC)
    bad["exits"]["stop_pct"] = 50
    r = client.post("/strategies", json={"spec": bad}, headers=h)
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "stop_pct"
    missing = {k: v for k, v in SPEC.items() if k != "schema_version"}
    r = client.post("/strategies", json={"spec": missing}, headers=h)
    assert r.status_code == 422 and "schema_version" in r.json()["detail"]
    future = {**SPEC, "schema_version": 9}
    assert client.post("/strategies", json={"spec": future}, headers=h).status_code == 422
    assert client.post("/strategies", json={"spec": "x"}, headers=h).status_code == 422


def test_strategy_cannot_use_other_users_account(make_user, client):
    alice, bob = make_user("alice@example.com"), make_user("bob@example.com")
    a = client.post("/accounts", json={"broker_id": "upstox", "label": "Main"}, headers=alice.headers).json()
    r = client.post("/strategies", json={"spec": SPEC, "broker_account_id": a["id"]}, headers=bob.headers)
    assert r.status_code == 404
    s = client.post("/strategies", json={"spec": SPEC}, headers=bob.headers).json()
    assert (
        client.patch(f"/strategies/{s['id']}", json={"broker_account_id": a["id"]}, headers=bob.headers).status_code
        == 404
    )
    assert client.get(f"/strategies/{s['id']}", headers=alice.headers).status_code == 404
    assert client.get(f"/strategies/{s['id']}/versions", headers=alice.headers).status_code == 404


# ----- risk -----------------------------------------------------------------------------------------------------


def test_risk_limits(user, client):
    h = user.headers
    r = client.get("/risk", headers=h).json()
    assert r["max_orders_per_second"] == 10 and r["daily_loss_limit"] is None and r["updated_at"] is None
    body = {"daily_loss_limit": "5000.50", "max_trades_per_day": 20, "stop_new_entries_after": "14:30"}
    r = client.put("/risk", json=body, headers=h)
    assert r.status_code == 200 and r.json()["daily_loss_limit"] == "5000.50"
    r = client.put("/risk", json={**body, "kill_switch": True}, headers=h)
    assert r.json()["kill_switch"] is True
    assert client.get("/risk", headers=h).json()["stop_new_entries_after"] == "14:30"
    assert audit_actions(user.email)[-2:] == ["risk.create", "risk.update"]


@pytest.mark.parametrize(
    "body",
    [
        {"max_orders_per_second": 11},
        {"stop_new_entries_after": "25:00"},
        {"daily_loss_limit": -1},
        {"max_trades_per_day": 0},
        {"unknown": 1},
    ],
)
def test_risk_validation(user, client, body):
    assert client.put("/risk", json=body, headers=user.headers).status_code == 422


def test_audit_log_is_append_only(user):
    with get_engine().connect() as conn, pytest.raises(Exception, match="append-only"):
        conn.execute(text("UPDATE audit_log SET action = 'x'"))
    with get_engine().connect() as conn, pytest.raises(Exception, match="append-only"):
        conn.execute(text("DELETE FROM audit_log"))
    # Deleting a user keeps its history (user_id set to NULL).
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM users"))
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM audit_log WHERE user_id IS NULL")).scalar_one() >= 3
