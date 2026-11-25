"""Sign-in: email one-time code, then a mandatory authenticator-app (TOTP) code.

Flow:
1. ``POST /auth/otp/request`` emails a 6-digit code (always 202, so it does not reveal who has an account).
2. ``POST /auth/otp/verify`` checks it and returns a short-lived ``mfa_token``. First sign-in: ``stage=totp_enroll``
   with a new TOTP secret and otpauth URI to scan; afterwards ``stage=totp_verify``.
3. ``POST /auth/totp/confirm`` (enrolment) or ``POST /auth/totp/verify`` returns access + refresh tokens.
4. ``POST /auth/refresh`` rotates the refresh token; re-using an old one revokes the whole token family.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import audit
from ..config import Settings, get_settings
from ..db import get_db
from ..deps import current_user
from ..emailer import otp_email
from ..models import EmailOTP, RefreshToken, User
from ..ratelimit import client_ip, enforce, per_ip
from ..schemas import MfaStep, OtpRequest, OtpVerify, RefreshIn, TokenPair, TotpSubmit, UserOut
from ..security import (
    TokenError,
    decode_jwt,
    decrypt_secret,
    encrypt_secret,
    issue_jwt,
    keyed_hash,
    new_otp_code,
    new_refresh_token,
    new_totp_secret,
    totp_uri,
    verify_totp,
)

router = APIRouter(prefix="/auth", tags=["auth"])
BAD_CODE = HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid or expired code")
FIFTEEN_MIN = 15 * 60


def _now() -> datetime:
    return datetime.now(UTC)


@router.post(
    "/otp/request", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(per_ip("otp-req", 20, FIFTEEN_MIN))]
)
def request_otp(
    body: OtpRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    email = body.email.strip().lower()
    enforce(request, f"otp-req:email:{email}", 5, FIFTEEN_MIN)
    now = _now()
    db.execute(update(EmailOTP).where(EmailOTP.email == email, EmailOTP.consumed_at.is_(None)).values(consumed_at=now))
    code = new_otp_code()
    db.add(
        EmailOTP(
            email=email,
            code_hash=keyed_hash(settings, "otp", f"{email}:{code}"),
            expires_at=now + timedelta(minutes=settings.otp_ttl_minutes),
        )
    )
    user = db.scalar(select(User).where(User.email == email))
    audit.record(db, request, action="auth.otp_requested", user_id=user.id if user else None, entity_type="user")
    db.commit()
    request.app.state.email_sender.send(otp_email(email, code, settings.otp_ttl_minutes))
    return {"detail": "If that address can receive email, a sign-in code is on its way."}


@router.post("/otp/verify", dependencies=[Depends(per_ip("otp-verify", 30, FIFTEEN_MIN))])
def verify_otp(
    body: OtpVerify,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MfaStep:
    email = body.email.strip().lower()
    enforce(request, f"otp-verify:email:{email}", 10, FIFTEEN_MIN)
    now = _now()
    otp = db.scalar(
        select(EmailOTP)
        .where(EmailOTP.email == email, EmailOTP.consumed_at.is_(None), EmailOTP.expires_at > now)
        .order_by(EmailOTP.created_at.desc())
        .with_for_update()
        .limit(1)
    )
    if otp is None:
        raise BAD_CODE
    expected = keyed_hash(settings, "otp", f"{email}:{body.code}")
    if not hmac.compare_digest(otp.code_hash, expected):
        otp.attempts += 1
        if otp.attempts >= settings.otp_max_attempts:
            otp.consumed_at = now
        user = db.scalar(select(User).where(User.email == email))
        audit.record(
            db,
            request,
            action="auth.login_failed",
            user_id=user.id if user else None,
            entity_type="user",
            after={"reason": "bad_email_code"},
        )
        db.commit()
        raise BAD_CODE
    otp.consumed_at = now
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, settings={})
        db.add(user)
        db.flush()
        audit.record(
            db,
            request,
            action="user.create",
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            after={"email": email},
        )
    if not user.is_active:
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This account is disabled")
    ttl = timedelta(minutes=settings.mfa_ttl_minutes)
    if not user.totp_enabled:
        secret = new_totp_secret()
        user.totp_secret_enc = encrypt_secret(settings, secret)
        user.totp_last_step = None
        db.commit()
        return MfaStep(
            stage="totp_enroll",
            mfa_token=issue_jwt(settings, sub=str(user.id), typ="mfa", ttl=ttl, extra={"stage": "enroll"}),
            totp_secret=secret,
            otpauth_uri=totp_uri(settings, secret, email),
        )
    db.commit()
    return MfaStep(
        stage="totp_verify",
        mfa_token=issue_jwt(settings, sub=str(user.id), typ="mfa", ttl=ttl, extra={"stage": "verify"}),
    )


def _mfa_user(db: Session, settings: Settings, token: str, stage: str) -> User:
    try:
        payload = decode_jwt(settings, token, typ="mfa")
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sign-in step expired; start again") from None
    if payload.get("stage") != stage:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Wrong sign-in step")
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_active or user.totp_secret_enc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sign-in step expired; start again")
    return user


def _issue_tokens(
    db: Session, request: Request, settings: Settings, user: User, family: uuid.UUID
) -> tuple[TokenPair, RefreshToken]:
    raw = new_refresh_token()
    rt = RefreshToken(
        user_id=user.id,
        family_id=family,
        token_hash=keyed_hash(settings, "refresh", raw),
        expires_at=_now() + timedelta(days=settings.refresh_ttl_days),
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:256],
    )
    db.add(rt)
    db.flush()
    access = issue_jwt(settings, sub=str(user.id), typ="access", ttl=timedelta(minutes=settings.access_ttl_minutes))
    return TokenPair(access_token=access, refresh_token=raw, expires_in=settings.access_ttl_minutes * 60), rt


def _login(db: Session, request: Request, settings: Settings, user: User) -> TokenPair:
    pair, _ = _issue_tokens(db, request, settings, user, uuid.uuid4())
    user.last_login_at = _now()
    audit.record(db, request, action="auth.login", user_id=user.id, entity_type="user", entity_id=user.id)
    db.commit()
    return pair


def _check_totp(db: Session, request: Request, settings: Settings, user: User, code: str) -> None:
    enforce(request, f"totp:user:{user.id}", 10, FIFTEEN_MIN)
    step = verify_totp(decrypt_secret(settings, user.totp_secret_enc or ""), code, user.totp_last_step)
    if step is None:
        audit.record(
            db, request, action="auth.login_failed", user_id=user.id, entity_type="user", after={"reason": "bad_totp"}
        )
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid authenticator code")
    user.totp_last_step = step


@router.post("/totp/confirm", dependencies=[Depends(per_ip("totp", 30, FIFTEEN_MIN))])
def confirm_totp(
    body: TotpSubmit, request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> TokenPair:
    user = _mfa_user(db, settings, body.mfa_token, "enroll")
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Authenticator already set up")
    _check_totp(db, request, settings, user, body.code)
    user.totp_enabled = True
    audit.record(db, request, action="auth.totp_enrolled", user_id=user.id, entity_type="user", entity_id=user.id)
    return _login(db, request, settings, user)


@router.post("/totp/verify", dependencies=[Depends(per_ip("totp", 30, FIFTEEN_MIN))])
def verify_totp_code(
    body: TotpSubmit, request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> TokenPair:
    user = _mfa_user(db, settings, body.mfa_token, "verify")
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Authenticator not set up")
    _check_totp(db, request, settings, user, body.code)
    return _login(db, request, settings, user)


UNAUTH_REFRESH = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session expired; sign in again")


@router.post("/refresh", dependencies=[Depends(per_ip("refresh", 60, 60))])
def refresh(
    body: RefreshIn, request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> TokenPair:
    token_hash = keyed_hash(settings, "refresh", body.refresh_token)
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update())
    if rt is None:
        raise UNAUTH_REFRESH
    now = _now()
    if rt.revoked_at is not None:
        # Reuse of a rotated token: assume theft and end every session in this family.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == rt.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        audit.record(
            db,
            request,
            action="auth.refresh_reuse",
            user_id=rt.user_id,
            entity_type="refresh_token",
            entity_id=rt.family_id,
        )
        db.commit()
        raise UNAUTH_REFRESH
    user = db.get(User, rt.user_id)
    if rt.expires_at <= now or user is None or not user.is_active:
        raise UNAUTH_REFRESH
    pair, new_rt = _issue_tokens(db, request, settings, user, rt.family_id)
    rt.revoked_at = now
    rt.replaced_by_id = new_rt.id
    db.commit()
    return pair


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(per_ip("refresh", 60, 60))])
def logout(
    body: RefreshIn, request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> Response:
    rt = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == keyed_hash(settings, "refresh", body.refresh_token))
    )
    if rt is not None:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == rt.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        audit.record(db, request, action="auth.logout", user_id=rt.user_id, entity_type="user", entity_id=rt.user_id)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.model_validate(user)
