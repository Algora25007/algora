"""Tokens, hashes and TOTP."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from cryptography.fernet import Fernet, InvalidToken

from .config import Settings

ALGORITHM = "HS256"
TOTP_INTERVAL = 30


class TokenError(Exception):
    pass


def _key(settings: Settings, purpose: str) -> bytes:
    """Derive a per-purpose key from the master secret so one leaked hash type does not reveal another."""
    return hmac.new(settings.secret_key.get_secret_value().encode(), purpose.encode(), hashlib.sha256).digest()


def keyed_hash(settings: Settings, purpose: str, value: str) -> str:
    return hmac.new(_key(settings, purpose), value.encode(), hashlib.sha256).hexdigest()


def new_otp_code(length: int = 6) -> str:
    return f"{secrets.randbelow(10**length):0{length}d}"


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def issue_jwt(settings: Settings, *, sub: str, typ: str, ttl: timedelta, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "typ": typ,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
        **(extra or {}),
    }
    return jwt.encode(payload, _key(settings, "jwt"), algorithm=ALGORITHM)


def decode_jwt(settings: Settings, token: str, *, typ: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            _key(settings, "jwt"),
            algorithms=[ALGORITHM],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "typ", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise TokenError(str(e)) from e
    if payload.get("typ") != typ:
        raise TokenError("wrong token type")
    return payload


def _fernet(settings: Settings) -> Fernet:
    return Fernet(settings.totp_encryption_key.get_secret_value().encode())


def new_totp_secret() -> str:
    return pyotp.random_base32()


def encrypt_secret(settings: Settings, secret: str) -> str:
    return _fernet(settings).encrypt(secret.encode()).decode()


def decrypt_secret(settings: Settings, blob: str) -> str:
    try:
        return _fernet(settings).decrypt(blob.encode()).decode()
    except InvalidToken as e:  # pragma: no cover - only on key rotation mistakes
        raise TokenError("cannot decrypt TOTP secret") from e


def totp_uri(settings: Settings, secret: str, email: str) -> str:
    return pyotp.TOTP(secret, interval=TOTP_INTERVAL).provisioning_uri(name=email, issuer_name=settings.totp_issuer)


def verify_totp(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """Return the matched time step if ``code`` is valid (±1 step drift) and newer than ``last_step``.

    Refusing a step at or before the last accepted one stops a code from being replayed.
    """
    if not code.isdigit() or len(code) != 6:
        return None
    t = time.time() if now is None else now
    totp = pyotp.TOTP(secret, interval=TOTP_INTERVAL)
    current = int(t // TOTP_INTERVAL)
    for step in (current - 1, current, current + 1):
        if hmac.compare_digest(totp.at(step * TOTP_INTERVAL), code):
            if last_step is not None and step <= last_step:
                return None
            return step
    return None
