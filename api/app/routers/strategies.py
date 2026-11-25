"""Strategies: DSL documents with immutable versions."""

from __future__ import annotations

import uuid
from typing import Any

from algora_engine.dsl import SpecVersionError, StrategySpec, load_spec
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import current_user
from ..models import Strategy, StrategyVersion, User
from ..schemas import Page, StrategyCreate, StrategyOut, StrategyPatch, StrategyUpdate, StrategyVersionOut
from .accounts import get_owned

router = APIRouter(prefix="/strategies", tags=["strategies"])
MAX_STRATEGIES = 200


def validate_spec(doc: dict[str, Any]) -> StrategySpec:
    try:
        return load_spec(doc)
    except SpecVersionError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None
    except ValidationError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=jsonable_encoder(e.errors(include_url=False, include_context=False)),
        ) from None


def spec_json(spec: StrategySpec) -> dict[str, Any]:
    return spec.model_dump(mode="json")


def current_spec(s: Strategy) -> dict[str, Any]:
    return next(v.spec for v in s.versions if v.version == s.current_version)


def out(s: Strategy) -> StrategyOut:
    return StrategyOut(
        id=s.id,
        name=s.name,
        template=s.template,
        paused=s.paused,
        broker_account_id=s.broker_account_id,
        current_version=s.current_version,
        spec=current_spec(s),
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def snapshot(s: Strategy) -> dict[str, Any]:
    return {
        "name": s.name,
        "paused": s.paused,
        "version": s.current_version,
        "broker_account_id": str(s.broker_account_id) if s.broker_account_id else None,
    }


def get_owned_strategy(db: Session, user: User, strategy_id: uuid.UUID) -> Strategy:
    s = db.scalar(select(Strategy).where(Strategy.id == strategy_id, Strategy.user_id == user.id))
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    return s


def create_strategy_row(
    db: Session, user: User, spec: StrategySpec, *, paused: bool = False, broker_account_id: uuid.UUID | None = None
) -> Strategy:
    s = Strategy(
        user_id=user.id,
        name=spec.name,
        template=spec.template,
        paused=paused,
        broker_account_id=broker_account_id,
        current_version=1,
    )
    s.versions.append(StrategyVersion(version=1, schema_version=spec.schema_version, spec=spec_json(spec)))
    db.add(s)
    db.flush()
    return s


def strategy_count(db: Session, user: User) -> int:
    return db.scalar(select(func.count()).select_from(Strategy).where(Strategy.user_id == user.id)) or 0


@router.get("")
def list_strategies(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Page[StrategyOut]:
    q = select(Strategy).where(Strategy.user_id == user.id).order_by(Strategy.created_at, Strategy.id)
    rows = db.scalars(q.limit(limit).offset(offset)).all()
    return Page[StrategyOut](items=[out(s) for s in rows], total=strategy_count(db, user), limit=limit, offset=offset)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_strategy(
    body: StrategyCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> StrategyOut:
    spec = validate_spec(body.spec)
    if strategy_count(db, user) >= MAX_STRATEGIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"At most {MAX_STRATEGIES} strategies")
    if body.broker_account_id:
        get_owned(db, user, body.broker_account_id)
    s = create_strategy_row(db, user, spec, paused=body.paused, broker_account_id=body.broker_account_id)
    audit.record(
        db,
        request,
        action="strategy.create",
        user_id=user.id,
        entity_type="strategy",
        entity_id=s.id,
        after={**snapshot(s), "spec": spec_json(spec)},
    )
    db.commit()
    return out(s)


@router.get("/{strategy_id}")
def get_strategy(
    strategy_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> StrategyOut:
    return out(get_owned_strategy(db, user, strategy_id))


@router.get("/{strategy_id}/versions")
def list_versions(
    strategy_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[StrategyVersionOut]:
    s = get_owned_strategy(db, user, strategy_id)
    return [StrategyVersionOut.model_validate(v) for v in s.versions]


@router.put("/{strategy_id}")
def update_rules(
    strategy_id: uuid.UUID,
    body: StrategyUpdate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> StrategyOut:
    s = get_owned_strategy(db, user, strategy_id)
    spec = validate_spec(body.spec)
    before = {**snapshot(s), "spec": current_spec(s)}
    if spec_json(spec) == current_spec(s):
        return out(s)  # no change, no new version
    s.current_version += 1
    s.name = spec.name
    s.template = spec.template
    s.versions.append(
        StrategyVersion(version=s.current_version, schema_version=spec.schema_version, spec=spec_json(spec))
    )
    db.flush()
    audit.record(
        db,
        request,
        action="strategy.update",
        user_id=user.id,
        entity_type="strategy",
        entity_id=s.id,
        before=before,
        after={**snapshot(s), "spec": spec_json(spec)},
    )
    db.commit()
    return out(s)


@router.patch("/{strategy_id}")
def patch_strategy(
    strategy_id: uuid.UUID,
    body: StrategyPatch,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> StrategyOut:
    s = get_owned_strategy(db, user, strategy_id)
    before = snapshot(s)
    if body.paused is not None:
        s.paused = body.paused
    if body.clear_broker_account:
        s.broker_account_id = None
    elif body.broker_account_id is not None:
        s.broker_account_id = get_owned(db, user, body.broker_account_id).id
    db.flush()
    audit.record(
        db,
        request,
        action="strategy.update",
        user_id=user.id,
        entity_type="strategy",
        entity_id=s.id,
        before=before,
        after=snapshot(s),
    )
    db.commit()
    return out(s)


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_strategy(
    strategy_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    s = get_owned_strategy(db, user, strategy_id)
    audit.record(
        db,
        request,
        action="strategy.delete",
        user_id=user.id,
        entity_type="strategy",
        entity_id=s.id,
        before={**snapshot(s), "spec": current_spec(s)},
    )
    db.delete(s)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
