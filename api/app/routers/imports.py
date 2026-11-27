"""Migrate a user's browser data: accepts the static site's export (``algora-backup.json``, version 1)."""

from __future__ import annotations

import json
import re
from typing import Any

from algora_engine.browser_import import SUPPORTED, bot_key, clean_accounts, convert_bot, split_account_label
from algora_engine.dsl import load_spec
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import user_limit
from ..models import BrokerAccount, RiskLimits, Strategy, User
from ..schemas import ImportedStrategy, ImportResult, RiskIn
from .accounts import MAX_ACCOUNTS
from .accounts import snapshot as account_snapshot
from .risk import upsert as upsert_risk
from .strategies import MAX_STRATEGIES, create_strategy_row, current_spec, spec_json, strategy_count
from .strategies import snapshot as strategy_snapshot

router = APIRouter(tags=["import"])
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_BT_RANGE = {
    "buf": (0, 5),
    "exitv": (0.05, 20),
    "max": (1, 50),
    "lot": (1, 100000),
    "delta": (0.01, 1),
    "cost": (0, 100000),
}


def clean_backtest_settings(s: Any) -> dict[str, str]:
    """The browser's cleanBt: keep only valid backtest form values (stored as strings, as the form does)."""
    out: dict[str, str] = {}
    if not isinstance(s, dict):
        return out
    if s.get("strategy") in SUPPORTED:
        out["strategy"] = s["strategy"]
    for k in ("start", "sq"):
        if isinstance(s.get(k), str) and _TIME.match(s[k]):
            out[k] = s[k]
    for k, (lo, hi) in _BT_RANGE.items():
        v = s.get(k)
        try:
            f = float(v) if isinstance(v, str | int | float) and not isinstance(v, bool) else None
        except ValueError:
            f = None
        if f is not None and lo <= f <= hi:
            out[k] = str(int(f)) if f.is_integer() else str(f)
    if s.get("exit") in ("flip", "target", "stop"):
        out["exit"] = s["exit"]
    return out


def clean_risk(r: Any) -> RiskIn | None:
    """The dashboard's risk card: {loss, trades, cut}."""
    if not isinstance(r, dict):
        return None
    kw: dict[str, Any] = {}
    loss, trades, cut = r.get("loss"), r.get("trades"), r.get("cut")
    if isinstance(loss, int | float) and not isinstance(loss, bool) and 0 <= loss <= 1e8:
        kw["daily_loss_limit"] = round(loss)
    if isinstance(trades, int) and not isinstance(trades, bool) and 1 <= trades <= 500:
        kw["max_trades_per_day"] = trades
    if isinstance(cut, str) and _TIME.match(cut):
        kw["stop_new_entries_after"] = cut
    return RiskIn(**kw) if kw else None


@router.post("/import")
def import_browser_export(
    request: Request,
    data: dict[str, Any] = Body(..., description="The JSON downloaded from the site's Export button"),
    user: User = Depends(user_limit("import", 10, 3600)),
    db: Session = Depends(get_db),
) -> ImportResult:
    if data.get("app") != "algora" or data.get("version") != 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Not an Algora backup (version 1)")

    existing = {
        (a.broker_id, a.label): a for a in db.scalars(select(BrokerAccount).where(BrokerAccount.user_id == user.id))
    }
    created_accounts = existing_accounts = 0

    def ensure_account(broker_id: str, label: str) -> BrokerAccount | None:
        nonlocal created_accounts, existing_accounts
        acct = existing.get((broker_id, label))
        if acct is not None:
            existing_accounts += 1
            return acct
        if len(existing) >= MAX_ACCOUNTS:
            return None
        acct = BrokerAccount(user_id=user.id, broker_id=broker_id, label=label)
        db.add(acct)
        db.flush()
        audit.record(
            db,
            request,
            action="account.create",
            user_id=user.id,
            entity_type="broker_account",
            entity_id=acct.id,
            after={**account_snapshot(acct), "source": "browser_import"},
        )
        existing[(broker_id, label)] = acct
        created_accounts += 1
        return acct

    for broker_id, labels in clean_accounts(data.get("accounts")).items():
        for label in labels:
            ensure_account(broker_id, label)

    current: set[tuple[str, str]] = set()
    for s in db.scalars(select(Strategy).where(Strategy.user_id == user.id)):
        try:  # normalise through the model so JSONB number formatting cannot hide a duplicate
            current.add((s.name, _canon(spec_json(load_spec(current_spec(s))))))
        except ValueError:  # pragma: no cover - stored specs were validated on write
            continue
    n_strats = strategy_count(db, user)
    created_strats = 0
    details: list[ImportedStrategy] = []
    skipped: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    bots = data.get("bots")
    for raw in (bots if isinstance(bots, list) else [])[:200]:
        name = str(raw.get("name", ""))[:80] if isinstance(raw, dict) else ""
        try:
            conv = convert_bot(raw)
        except ValueError as e:
            skipped.append({"name": name, "reason": str(e)})
            continue
        key = bot_key(raw)
        doc = spec_json(conv.spec)
        if key in seen_keys or (conv.spec.name, _canon(doc)) in current:
            skipped.append({"name": name, "reason": "already imported"})
            continue
        if n_strats >= MAX_STRATEGIES:
            skipped.append({"name": name, "reason": f"limit of {MAX_STRATEGIES} strategies reached"})
            continue
        seen_keys.add(key)
        acct_id = None
        if conv.account_label:
            parts = split_account_label(conv.account_label)
            acct = ensure_account(*parts) if parts else None
            acct_id = acct.id if acct else None
        s = create_strategy_row(db, user, conv.spec, paused=conv.paused, broker_account_id=acct_id)
        audit.record(
            db,
            request,
            action="strategy.create",
            user_id=user.id,
            entity_type="strategy",
            entity_id=s.id,
            after={**strategy_snapshot(s), "spec": doc, "source": "browser_import"},
        )
        current.add((conv.spec.name, _canon(doc)))
        details.append(
            ImportedStrategy(
                id=s.id,
                name=conv.spec.name,
                browser_strategy=str(raw["strategy"]),
                template=conv.spec.template,
                defaults_used=conv.defaults_used,
                notes=list(conv.notes),
            )
        )
        n_strats += 1
        created_strats += 1

    risk = clean_risk(data.get("risk"))
    risk_imported = False
    if risk is not None and db.get(RiskLimits, user.id) is None:  # never overwrite limits set on the server
        upsert_risk(db, request, user, risk)
        risk_imported = True
    bt = clean_backtest_settings(data.get("backtest"))
    if bt:
        before = dict(user.settings or {})
        user.settings = {**before, "browser_backtest_defaults": bt}
        audit.record(
            db,
            request,
            action="user.update",
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            before={"settings": before},
            after={"settings": user.settings},
        )
    result = ImportResult(
        accounts_created=created_accounts,
        accounts_existing=existing_accounts,
        strategies_created=created_strats,
        strategies_created_details=details,
        strategies_skipped=skipped,
        risk_imported=risk_imported,
        backtest_settings_imported=bool(bt),
    )
    audit.record(
        db,
        request,
        action="import.browser",
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        after=result.model_dump(mode="json"),
    )
    db.commit()
    return result


def _canon(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))
