from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import current_user
from ..models import RiskLimits, User
from ..schemas import RiskIn, RiskOut

router = APIRouter(prefix="/risk", tags=["risk limits"])


def snapshot(r: RiskLimits) -> dict:
    return RiskIn(
        daily_loss_limit=r.daily_loss_limit,
        max_trades_per_day=r.max_trades_per_day,
        stop_new_entries_after=r.stop_new_entries_after,
        max_orders_per_second=r.max_orders_per_second,
        kill_switch=r.kill_switch,
    ).model_dump(mode="json")


def upsert(db: Session, request: Request, user: User, body: RiskIn) -> RiskLimits:
    r = db.get(RiskLimits, user.id)
    before = snapshot(r) if r else None
    if r is None:
        r = RiskLimits(user_id=user.id)
        db.add(r)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    db.flush()
    audit.record(
        db,
        request,
        action="risk.create" if before is None else "risk.update",
        user_id=user.id,
        entity_type="risk_limits",
        entity_id=user.id,
        before=before,
        after=snapshot(r),
    )
    return r


@router.get("")
def get_risk(user: User = Depends(current_user), db: Session = Depends(get_db)) -> RiskOut:
    r = db.get(RiskLimits, user.id)
    return RiskOut.model_validate(r) if r else RiskOut()


@router.put("")
def put_risk(
    body: RiskIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> RiskOut:
    r = upsert(db, request, user, body)
    db.commit()
    return RiskOut.model_validate(r)
