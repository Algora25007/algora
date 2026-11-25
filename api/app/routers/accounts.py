"""Broker account labels. Phase 1 stores only broker id + label; never API keys or secrets."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import current_user
from ..models import BrokerAccount, User
from ..schemas import AccountIn, AccountOut, AccountPatch

router = APIRouter(prefix="/accounts", tags=["broker accounts"])
MAX_ACCOUNTS = 200


def snapshot(a: BrokerAccount) -> dict:
    return {"broker_id": a.broker_id, "label": a.label}


def get_owned(db: Session, user: User, account_id: uuid.UUID) -> BrokerAccount:
    acct = db.scalar(select(BrokerAccount).where(BrokerAccount.id == account_id, BrokerAccount.user_id == user.id))
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found")
    return acct


DUPLICATE = HTTPException(status.HTTP_409_CONFLICT, detail="You already have an account with that label")


@router.get("")
def list_accounts(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[AccountOut]:
    rows = db.scalars(select(BrokerAccount).where(BrokerAccount.user_id == user.id).order_by(BrokerAccount.created_at))
    return [AccountOut.model_validate(a) for a in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_account(
    body: AccountIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> AccountOut:
    count = len(db.scalars(select(BrokerAccount.id).where(BrokerAccount.user_id == user.id)).all())
    if count >= MAX_ACCOUNTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"At most {MAX_ACCOUNTS} accounts")
    acct = BrokerAccount(user_id=user.id, broker_id=body.broker_id, label=body.label)
    db.add(acct)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise DUPLICATE from None
    audit.record(
        db,
        request,
        action="account.create",
        user_id=user.id,
        entity_type="broker_account",
        entity_id=acct.id,
        after=snapshot(acct),
    )
    db.commit()
    return AccountOut.model_validate(acct)


@router.patch("/{account_id}")
def rename_account(
    account_id: uuid.UUID,
    body: AccountPatch,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AccountOut:
    acct = get_owned(db, user, account_id)
    before = snapshot(acct)
    acct.label = body.label
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise DUPLICATE from None
    audit.record(
        db,
        request,
        action="account.update",
        user_id=user.id,
        entity_type="broker_account",
        entity_id=acct.id,
        before=before,
        after=snapshot(acct),
    )
    db.commit()
    return AccountOut.model_validate(acct)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    acct = get_owned(db, user, account_id)
    audit.record(
        db,
        request,
        action="account.delete",
        user_id=user.id,
        entity_type="broker_account",
        entity_id=acct.id,
        before=snapshot(acct),
    )
    db.delete(acct)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
