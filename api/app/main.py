"""FastAPI application factory."""

from __future__ import annotations

import logging

from algora_engine import __version__ as engine_version
from algora_engine.charges import rate_table
from algora_engine.dsl import json_schema
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .config import Settings, get_settings
from .db import get_db
from .emailer import EmailSender, build_sender
from .ratelimit import MemoryRateLimiter, RateLimiter, RedisRateLimiter, per_ip
from .routers import accounts, auth, backtests, imports, risk, strategies

log = logging.getLogger("algora.api")


class BodySizeLimit:
    """Reject request bodies over a size limit before they are read (413), and bodies with no length (411)."""

    def __init__(self, app: ASGIApp, default_limit: int, import_limit: int) -> None:
        self.app = app
        self.default_limit = default_limit
        self.import_limit = import_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in ("POST", "PUT", "PATCH"):
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            limit = self.import_limit if scope["path"].rstrip("/").endswith("/import") else self.default_limit
            length = headers.get("content-length")
            status_code = None
            if length is None:
                status_code = 411 if "chunked" in headers.get("transfer-encoding", "") else None
            elif not length.isdigit() or int(length) > limit:
                status_code = 413
            if status_code:
                detail = "Request body too large" if status_code == 413 else "Content-Length required"
                await JSONResponse({"detail": detail}, status_code=status_code)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"cache-control", b"no-store"),
                    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
                ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(
    settings: Settings | None = None,
    *,
    email_sender: EmailSender | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Algora API",
        version=__version__,
        description="Strategies, backtests and accounts for Algora. Sign in with an email code plus an authenticator.",
        docs_url="/docs" if settings.env != "prod" else None,
        redoc_url=None,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.email_sender = email_sender or build_sender(settings)
    if rate_limiter is None:
        rate_limiter = (
            RedisRateLimiter(settings.redis_url or "")
            if settings.rate_limit_backend == "redis"
            else MemoryRateLimiter()
        )
    app.state.rate_limiter = rate_limiter
    app.state.trust_forwarded_for = settings.trust_forwarded_for

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, no cookies
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    app.add_middleware(
        BodySizeLimit, default_limit=settings.max_upload_bytes + 64 * 1024, import_limit=settings.max_import_bytes
    )
    app.add_middleware(SecurityHeaders)

    for r in (auth.router, accounts.router, strategies.router, risk.router, backtests.router, imports.router):
        app.include_router(r)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover - safety net
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal error"}, status_code=500)

    @app.get("/health", tags=["meta"])
    def health(db: Session = Depends(get_db)) -> dict:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "api": __version__, "engine": engine_version}

    @app.get("/meta/charges", tags=["meta"], dependencies=[Depends(per_ip("meta", 60, 60))])
    def charges() -> dict:
        """Every statutory/exchange rate the engine uses, with its source and effective dates."""
        return {"rates": rate_table()}

    @app.get("/meta/strategy-schema", tags=["meta"], dependencies=[Depends(per_ip("meta", 60, 60))])
    def strategy_schema() -> dict:
        return json_schema()

    return app
