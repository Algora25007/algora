"""Cross-cutting: CORS, headers, body limits, rate limiter backends, settings, security helpers."""

import os
import socket

import pyotp
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.ratelimit import MemoryRateLimiter, RedisRateLimiter
from app.security import TokenError, decode_jwt, issue_jwt, keyed_hash, new_otp_code, verify_totp

PAGES = "https://algora25007.github.io"


def test_cors_allows_github_pages_only(client):
    pre = client.options(
        "/auth/otp/request",
        headers={
            "Origin": PAGES,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == PAGES
    other = client.options(
        "/auth/otp/request", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in other.headers
    r = client.get("/meta/charges", headers={"Origin": PAGES})
    assert r.headers["access-control-allow-origin"] == PAGES


def test_security_headers_and_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


def test_meta_endpoints(client):
    rates = client.get("/meta/charges").json()["rates"]
    assert any(x["component"] == "stt_sell" and x["pct"] == "0.05" for x in rates)
    schema = client.get("/meta/strategy-schema").json()
    assert schema["title"].startswith("Algora strategy")


def test_body_size_and_length_required(client):
    big = b"x" * (10 * 1024 * 1024 + 70 * 1024)
    r = client.post("/auth/otp/request", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    r = client.post(
        "/auth/otp/request",
        content=iter([b'{"email":', b'"a@example.com"}']),
        headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
    )
    assert r.status_code == 411
    r = client.post("/auth/otp/request", content=b"{}", headers={"Content-Length": "abc"})
    assert r.status_code in (400, 413)


def test_memory_rate_limiter_window():
    now = [0.0]
    rl = MemoryRateLimiter(clock=lambda: now[0])
    assert [rl.hit("k", 2, 10) for _ in range(3)] == [0, 0, 10]
    now[0] = 9.5
    assert rl.hit("k", 2, 10) == 1
    now[0] = 10.0
    assert rl.hit("k", 2, 10) == 0
    rl.reset()
    assert rl.hit("k", 1, 10) == 0


def _redis_url() -> str | None:
    url = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
    host_port = url.split("//", 1)[1].split("/", 1)[0]
    host, _, port = host_port.partition(":")
    try:
        socket.create_connection((host, int(port or 6379)), timeout=0.5).close()
    except OSError:
        return None
    return url


@pytest.mark.skipif(_redis_url() is None, reason="no Redis server reachable")
def test_redis_rate_limiter():
    rl = RedisRateLimiter(_redis_url())
    rl._r.delete("rl:test-key")
    assert [rl.hit("test-key", 2, 30) for _ in range(3)][:2] == [0, 0]
    assert 0 < rl.hit("test-key", 2, 30) <= 30
    rl._r.delete("rl:test-key")


def test_forwarded_for_trusted_only_when_enabled(app, client, limiter):
    app.state.trust_forwarded_for = True
    for _ in range(20):
        client.post("/auth/otp/request", json={"email": f"z{_}@example.com"}, headers={"X-Forwarded-For": "1.2.3.4"})
    r = client.post("/auth/otp/request", json={"email": "zz@example.com"}, headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 429
    r = client.post("/auth/otp/request", json={"email": "zz@example.com"}, headers={"X-Forwarded-For": "5.6.7.8"})
    assert r.status_code == 202


def _settings(**kw) -> Settings:
    base = {"secret_key": "s" * 40, "totp_encryption_key": "k" * 44}
    return Settings(**{**base, **kw})


def test_settings_validation():
    with pytest.raises(ValidationError, match="console is not allowed"):
        _settings(env="prod")
    with pytest.raises(ValidationError, match="redis rate limiter in prod"):
        _settings(env="prod", email_backend="smtp", smtp_host="smtp.example.com")
    with pytest.raises(ValidationError, match="SMTP_HOST"):
        _settings(email_backend="smtp")
    with pytest.raises(ValidationError, match="REDIS_URL"):
        _settings(rate_limit_backend="redis")
    with pytest.raises(ValidationError):
        _settings(secret_key="short")
    ok = _settings(
        env="prod",
        email_backend="smtp",
        smtp_host="smtp.example.com",
        rate_limit_backend="redis",
        redis_url="redis://r:6379/0",
    )
    assert ok.cors_origins == [PAGES]


def test_jwt_helpers():
    s = _settings()
    from datetime import timedelta

    tok = issue_jwt(s, sub="u", typ="access", ttl=timedelta(minutes=1))
    assert decode_jwt(s, tok, typ="access")["sub"] == "u"
    with pytest.raises(TokenError, match="wrong token type"):
        decode_jwt(s, tok, typ="mfa")
    with pytest.raises(TokenError):
        decode_jwt(_settings(secret_key="t" * 40), tok, typ="access")
    assert keyed_hash(s, "a", "x") != keyed_hash(s, "b", "x")


def test_totp_verification_window_and_replay():
    secret = pyotp.random_base32()
    t = pyotp.TOTP(secret)
    now = 1_800_000_000.0
    step = int(now // 30)
    assert verify_totp(secret, t.at(now), None, now) == step
    assert verify_totp(secret, t.at(now - 30), None, now) == step - 1  # one step of drift allowed
    assert verify_totp(secret, t.at(now - 90), None, now) is None
    assert verify_totp(secret, t.at(now), step, now) is None  # replay
    assert verify_totp(secret, "12345", None, now) is None
    assert verify_totp(secret, "abcdef", None, now) is None


def test_otp_codes_are_six_digits():
    codes = {new_otp_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes) and len(codes) > 150
