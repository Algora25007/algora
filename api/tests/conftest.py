"""API test fixtures. Needs a disposable Postgres database: TEST_DATABASE_URL (default below).

The schema is created by running the Alembic migrations, so the migrations are tested too.
"""

from __future__ import annotations

import os
import time

import pyotp
import pytest
from cryptography.fernet import Fernet

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://algora:algora@localhost:5432/algora_test")
# The suite drops and recreates the schema: refuse anything that is not clearly a test database.
if not TEST_DB.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
    raise RuntimeError(f"TEST_DATABASE_URL must point at a database whose name ends in _test, got {TEST_DB}")
os.environ.update(
    {
        "ALGORA_ENV": "test",
        "ALGORA_DATABASE_URL": TEST_DB,
        "ALGORA_SECRET_KEY": "test-secret-key-that-is-long-enough-0123456789",
        "ALGORA_TOTP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ALGORA_EMAIL_BACKEND": "console",
        "ALGORA_RATE_LIMIT_BACKEND": "memory",
    }
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.emailer import ConsoleEmailSender  # noqa: E402
from app.main import create_app  # noqa: E402
from app.ratelimit import MemoryRateLimiter  # noqa: E402

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLES = (
    "audit_log, backtest_runs, strategy_versions, strategies, broker_accounts, refresh_tokens, email_otps, "
    "risk_limits, users"
)


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.attributes["database_url"] = TEST_DB
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables():
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def outbox() -> ConsoleEmailSender:
    return ConsoleEmailSender()


@pytest.fixture
def limiter() -> MemoryRateLimiter:
    return MemoryRateLimiter()


@pytest.fixture
def app(outbox, limiter):
    return create_app(get_settings(), email_sender=outbox, rate_limiter=limiter)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def last_code(outbox: ConsoleEmailSender, email: str) -> str:
    msg = next(m for m in reversed(outbox.outbox) if m.to == email)
    return msg.subject.rsplit(" ", 1)[-1]


class Session:
    def __init__(self, client: TestClient, outbox: ConsoleEmailSender, email: str):
        self.client, self.outbox, self.email = client, outbox, email
        self.secret: str | None = None
        self.tokens: dict = {}
        self._offset = 0

    def totp(self) -> str:
        # Each login needs a fresh time step (replay protection); the server accepts ±1 step of drift.
        code = pyotp.TOTP(self.secret).at(time.time() + 30 * self._offset)
        self._offset += 1
        return code

    def login(self) -> dict:
        c = self.client
        assert c.post("/auth/otp/request", json={"email": self.email}).status_code == 202
        r = c.post("/auth/otp/verify", json={"email": self.email, "code": last_code(self.outbox, self.email)})
        assert r.status_code == 200, r.text
        step = r.json()
        if step["stage"] == "totp_enroll":
            self.secret = step["totp_secret"]
            r = c.post("/auth/totp/confirm", json={"mfa_token": step["mfa_token"], "code": self.totp()})
        else:
            r = c.post("/auth/totp/verify", json={"mfa_token": step["mfa_token"], "code": self.totp()})
        assert r.status_code == 200, r.text
        self.tokens = r.json()
        return self.tokens

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.tokens['access_token']}"}


@pytest.fixture
def make_user(client, outbox):
    def _make(email: str = "trader@example.com") -> Session:
        s = Session(client, outbox, email)
        s.login()
        return s

    return _make


@pytest.fixture
def user(make_user) -> Session:
    return make_user()


def audit_actions(user_email: str | None = None) -> list[str]:
    with get_engine().connect() as conn:
        if user_email is None:
            rows = conn.execute(text("SELECT action FROM audit_log ORDER BY id"))
        else:
            rows = conn.execute(
                text(
                    "SELECT a.action FROM audit_log a JOIN users u ON u.id = a.user_id WHERE u.email = :e ORDER BY a.id"
                ),
                {"e": user_email},
            )
        return [r[0] for r in rows]
