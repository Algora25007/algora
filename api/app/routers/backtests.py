"""Backtests: upload candles (CSV), run the engine, store the result."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from algora_engine.backtest import BacktestConfig, BacktestError, run_backtest
from algora_engine.candles import CandleFileError, parse_csv
from algora_engine.charges import RatesUnavailable
from algora_engine.lots import LotSizeUnknown
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import Settings, get_settings
from ..db import get_db
from ..deps import current_user, user_limit
from ..models import BacktestRun, User
from ..schemas import BacktestListItem, BacktestOut, Page
from .strategies import current_spec, get_owned_strategy, validate_spec

router = APIRouter(prefix="/backtests", tags=["backtests"])
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def _json_field(raw: str | None, what: str) -> Any:
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{what} must be valid JSON") from None


def _read_upload(file: UploadFile, limit: int) -> bytes:
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"File larger than {limit // 1024} KB")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file is empty")
    return data


def _list_item(r: BacktestRun) -> BacktestListItem:
    return BacktestListItem(
        id=r.id,
        strategy_id=r.strategy_id,
        strategy_version=r.strategy_version,
        name=r.spec.get("name", ""),
        candles_meta=r.candles_meta,
        summary=r.summary,
        created_at=r.created_at,
    )


def _full(r: BacktestRun) -> BacktestOut:
    return BacktestOut(
        **_list_item(r).model_dump(),
        spec=r.spec,
        config=r.config,
        equity_curve=r.equity_curve,
        per_day=r.per_day,
        trades=r.trades,
        warnings=r.warnings,
        engine_version=r.engine_version,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_backtest(
    request: Request,
    file: UploadFile = File(..., description="CSV with datetime, open, high, low, close[, volume]"),
    strategy_id: uuid.UUID | None = Form(None),
    spec: str | None = Form(None, description="Strategy JSON (schema v1), instead of strategy_id"),
    config: str | None = Form(None, description="BacktestConfig JSON: slippage, brokerage, risk, charges"),
    user: User = Depends(user_limit("backtest", 20, 60)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BacktestOut:
    if (strategy_id is None) == (spec is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Send exactly one of strategy_id or spec")
    version: int | None = None
    if strategy_id is not None:
        s = get_owned_strategy(db, user, strategy_id)
        parsed_spec = validate_spec(current_spec(s))
        version = s.current_version
    else:
        doc = _json_field(spec, "spec")
        if not isinstance(doc, dict):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="spec must be a JSON object")
        parsed_spec = validate_spec(doc)
    cfg_doc = _json_field(config, "config")
    try:
        cfg = BacktestConfig.model_validate(cfg_doc or {})
    except ValidationError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=jsonable_encoder(e.errors(include_url=False, include_context=False)),
        ) from None

    data = _read_upload(file, settings.max_upload_bytes)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file must be UTF-8 text (CSV)") from None
    try:
        candles = parse_csv(text, max_rows=settings.max_candles)
        result = run_backtest(parsed_spec, candles.bars, cfg)
    except (CandleFileError, BacktestError, LotSizeUnknown, RatesUnavailable) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None

    days = sorted({b.day for b in candles.bars})
    meta = {
        "file_name": _SAFE_NAME.sub("_", file.filename or "candles.csv")[:120],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows": len(candles.bars),
        "skipped": {
            "unreadable": candles.skipped_unreadable,
            "duplicate": candles.skipped_duplicate,
            "invalid": candles.skipped_invalid,
        },
        "timeframe_minutes": candles.timeframe_minutes,
        "sessions": candles.sessions,
        "from": days[0].isoformat(),
        "to": days[-1].isoformat(),
    }
    body = result.as_dict()
    run = BacktestRun(
        user_id=user.id,
        strategy_id=strategy_id,
        strategy_version=version,
        spec=parsed_spec.model_dump(mode="json"),
        config=cfg.model_dump(mode="json"),
        candles_meta={**meta, "engine": body["meta"]},
        summary=body["summary"],
        equity_curve=body["equity_curve"],
        per_day=body["per_day"],
        trades=body["trades"],
        warnings=body["warnings"],
        engine_version=body["meta"]["engine_version"],
    )
    db.add(run)
    db.flush()
    audit.record(
        db,
        request,
        action="backtest.create",
        user_id=user.id,
        entity_type="backtest_run",
        entity_id=run.id,
        after={
            "strategy_id": str(strategy_id) if strategy_id else None,
            "trades": body["summary"]["trades"],
            "file_sha256": meta["sha256"],
        },
    )
    db.commit()
    return _full(run)


@router.get("")
def list_backtests(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    strategy_id: uuid.UUID | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Page[BacktestListItem]:
    where = [BacktestRun.user_id == user.id]
    if strategy_id is not None:
        where.append(BacktestRun.strategy_id == strategy_id)
    total = db.scalar(select(func.count()).select_from(BacktestRun).where(*where)) or 0
    rows = db.scalars(
        select(BacktestRun)
        .where(*where)
        .order_by(BacktestRun.created_at.desc(), BacktestRun.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page[BacktestListItem](items=[_list_item(r) for r in rows], total=total, limit=limit, offset=offset)


def _owned_run(db: Session, user: User, run_id: uuid.UUID) -> BacktestRun:
    r = db.scalar(select(BacktestRun).where(BacktestRun.id == run_id, BacktestRun.user_id == user.id))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Backtest not found")
    return r


@router.get("/{run_id}")
def get_backtest(run_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> BacktestOut:
    return _full(_owned_run(db, user, run_id))


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backtest(
    run_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    r = _owned_run(db, user, run_id)
    audit.record(
        db,
        request,
        action="backtest.delete",
        user_id=user.id,
        entity_type="backtest_run",
        entity_id=r.id,
        before={"summary": r.summary},
    )
    db.delete(r)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
