from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import User
from .ratelimit import enforce
from .security import TokenError, decode_jwt

bearer = HTTPBearer(auto_error=False)

UNAUTHORIZED = HTTPException(
    status.HTTP_401_UNAUTHORIZED, detail="Not signed in", headers={"WWW-Authenticate": "Bearer"}
)


def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise UNAUTHORIZED
    try:
        payload = decode_jwt(settings, creds.credentials, typ="access")
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError):
        raise UNAUTHORIZED from None
    user = db.get(User, user_id)
    if user is None or not user.is_active or not user.totp_enabled:
        raise UNAUTHORIZED
    # Default per-user limit for every authenticated route.
    enforce(request, f"user:{user.id}", 240, 60)
    return user


def user_limit(scope: str, limit: int, window_seconds: int):
    def dep(request: Request, user: User = Depends(current_user)) -> User:
        enforce(request, f"{scope}:user:{user.id}", limit, window_seconds)
        return user

    return dep
