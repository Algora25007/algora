from datetime import UTC, datetime, timedelta

import pyotp
from conftest import audit_actions, last_code
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine
from app.security import issue_jwt


def sql(q: str, **params):
    with get_engine().begin() as conn:
        return conn.execute(text(q), params)


def test_first_login_enrols_totp_and_issues_tokens(client, outbox):
    r = client.post("/auth/otp/request", json={"email": "New.User@Example.com"})
    assert r.status_code == 202
    assert outbox.outbox[-1].to == "new.user@example.com"
    code = last_code(outbox, "new.user@example.com")
    assert len(code) == 6 and code.isdigit()
    step = client.post("/auth/otp/verify", json={"email": "new.user@example.com", "code": code}).json()
    assert step["stage"] == "totp_enroll"
    assert step["otpauth_uri"].startswith("otpauth://totp/Algora:new.user%40example.com")
    secret = step["totp_secret"]
    # Access is impossible before TOTP is confirmed: the mfa token is not an access token.
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {step['mfa_token']}"}).status_code == 401
    r = client.post("/auth/totp/confirm", json={"mfa_token": step["mfa_token"], "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200
    tokens = r.json()
    assert tokens["token_type"] == "bearer" and tokens["expires_in"] == 15 * 60
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert me["email"] == "new.user@example.com" and me["totp_enabled"] is True and me["last_login_at"]
    assert audit_actions("new.user@example.com") == [
        "user.create",
        "auth.totp_enrolled",
        "auth.login",
    ]
    # The secret is stored encrypted, never in plain text.
    stored = sql("SELECT totp_secret_enc FROM users").scalar_one()
    assert secret not in stored


def test_second_login_requires_totp_only(user, client, outbox):
    assert client.post("/auth/otp/request", json={"email": user.email}).status_code == 202
    step = client.post("/auth/otp/verify", json={"email": user.email, "code": last_code(outbox, user.email)}).json()
    assert step["stage"] == "totp_verify" and step["totp_secret"] is None
    r = client.post("/auth/totp/confirm", json={"mfa_token": step["mfa_token"], "code": "123456"})
    assert r.status_code == 400  # wrong step for this token
    r = client.post("/auth/totp/verify", json={"mfa_token": step["mfa_token"], "code": "000000"})
    assert r.status_code == 400 and "authenticator" in r.json()["detail"]
    code = user.totp()
    assert client.post("/auth/totp/verify", json={"mfa_token": step["mfa_token"], "code": code}).status_code == 200
    # Replaying the same code (same time step) is refused.
    assert client.post("/auth/totp/verify", json={"mfa_token": step["mfa_token"], "code": code}).status_code == 400
    assert "auth.login_failed" in audit_actions(user.email)


def test_confirm_twice_is_refused(client, outbox):
    client.post("/auth/otp/request", json={"email": "a@example.com"})
    step = client.post(
        "/auth/otp/verify", json={"email": "a@example.com", "code": last_code(outbox, "a@example.com")}
    ).json()
    secret = pyotp.TOTP(step["totp_secret"])
    assert (
        client.post("/auth/totp/confirm", json={"mfa_token": step["mfa_token"], "code": secret.now()}).status_code
        == 200
    )
    r = client.post(
        "/auth/totp/confirm",
        json={"mfa_token": step["mfa_token"], "code": secret.at(datetime.now(UTC) + timedelta(seconds=30))},
    )
    assert r.status_code == 400


def test_totp_verify_before_enrolment_is_refused(client):
    sql(
        "INSERT INTO users (id, email, is_active, totp_enabled, settings, created_at, totp_secret_enc) "
        "VALUES (gen_random_uuid(), 'x@example.com', true, false, '{}', now(), 'blob')"
    )
    uid = str(sql("SELECT id FROM users").scalar_one())
    tok = issue_jwt(get_settings(), sub=uid, typ="mfa", ttl=timedelta(minutes=5), extra={"stage": "verify"})
    assert client.post("/auth/totp/verify", json={"mfa_token": tok, "code": "123456"}).status_code == 400


def test_bad_mfa_tokens(client):
    r = client.post("/auth/totp/verify", json={"mfa_token": "not-a-jwt-token", "code": "123456"})
    assert r.status_code == 401
    expired = issue_jwt(
        get_settings(),
        sub="00000000-0000-0000-0000-000000000000",
        typ="mfa",
        ttl=timedelta(seconds=-1),
        extra={"stage": "verify"},
    )
    assert client.post("/auth/totp/verify", json={"mfa_token": expired, "code": "123456"}).status_code == 401
    unknown = issue_jwt(
        get_settings(),
        sub="00000000-0000-0000-0000-000000000000",
        typ="mfa",
        ttl=timedelta(minutes=1),
        extra={"stage": "verify"},
    )
    assert client.post("/auth/totp/verify", json={"mfa_token": unknown, "code": "123456"}).status_code == 401


def test_wrong_email_code_and_attempt_limit(client, outbox):
    client.post("/auth/otp/request", json={"email": "b@example.com"})
    good = last_code(outbox, "b@example.com")
    bad = "000000" if good != "000000" else "111111"
    for _ in range(5):
        r = client.post("/auth/otp/verify", json={"email": "b@example.com", "code": bad})
        assert r.status_code == 400
    # After 5 failures the code is burned, even the right one fails.
    assert client.post("/auth/otp/verify", json={"email": "b@example.com", "code": good}).status_code == 400
    assert audit_actions().count("auth.login_failed") == 5


def test_code_is_single_use_and_new_request_replaces_old(client, outbox):
    client.post("/auth/otp/request", json={"email": "c@example.com"})
    first = last_code(outbox, "c@example.com")
    client.post("/auth/otp/request", json={"email": "c@example.com"})
    second = last_code(outbox, "c@example.com")
    if first != second:
        assert client.post("/auth/otp/verify", json={"email": "c@example.com", "code": first}).status_code == 400
    assert client.post("/auth/otp/verify", json={"email": "c@example.com", "code": second}).status_code == 200
    assert client.post("/auth/otp/verify", json={"email": "c@example.com", "code": second}).status_code == 400


def test_expired_code(client, outbox):
    client.post("/auth/otp/request", json={"email": "d@example.com"})
    sql("UPDATE email_otps SET expires_at = now() - interval '1 minute'")
    code = last_code(outbox, "d@example.com")
    assert client.post("/auth/otp/verify", json={"email": "d@example.com", "code": code}).status_code == 400


def test_validation_errors(client):
    assert client.post("/auth/otp/request", json={"email": "not-an-email"}).status_code == 422
    assert client.post("/auth/otp/verify", json={"email": "a@example.com", "code": "12ab56"}).status_code == 422
    assert client.post("/auth/otp/request", json={"email": "a@example.com", "extra": 1}).status_code == 422


def test_disabled_user(user, client, outbox):
    sql("UPDATE users SET is_active = false")
    assert client.get("/auth/me", headers=user.headers).status_code == 401
    client.post("/auth/otp/request", json={"email": user.email})
    r = client.post("/auth/otp/verify", json={"email": user.email, "code": last_code(outbox, user.email)})
    assert r.status_code == 403
    assert client.post("/auth/refresh", json={"refresh_token": user.tokens["refresh_token"]}).status_code == 401


def test_access_token_checks(user, client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401
    tampered = user.tokens["access_token"][:-2] + ("AA" if not user.tokens["access_token"].endswith("AA") else "BB")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401
    bad_sub = issue_jwt(get_settings(), sub="not-a-uuid", typ="access", ttl=timedelta(minutes=1))
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {bad_sub}"}).status_code == 401


def test_refresh_rotation_and_reuse_detection(user, client):
    old = user.tokens["refresh_token"]
    r = client.post("/auth/refresh", json={"refresh_token": old})
    assert r.status_code == 200
    new = r.json()
    assert new["refresh_token"] != old
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {new['access_token']}"}).status_code == 200
    # Re-using the rotated token revokes the whole family, including the newest token.
    assert client.post("/auth/refresh", json={"refresh_token": old}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": new["refresh_token"]}).status_code == 401
    assert "auth.refresh_reuse" in audit_actions(user.email)
    assert client.post("/auth/refresh", json={"refresh_token": "x" * 40}).status_code == 401


def test_expired_refresh_token(user, client):
    sql("UPDATE refresh_tokens SET expires_at = now() - interval '1 second'")
    assert client.post("/auth/refresh", json={"refresh_token": user.tokens["refresh_token"]}).status_code == 401


def test_logout_revokes_session(user, client):
    assert client.post("/auth/logout", json={"refresh_token": user.tokens["refresh_token"]}).status_code == 204
    assert client.post("/auth/refresh", json={"refresh_token": user.tokens["refresh_token"]}).status_code == 401
    assert client.post("/auth/logout", json={"refresh_token": "y" * 40}).status_code == 204
    assert "auth.logout" in audit_actions(user.email)


def test_otp_request_rate_limited_per_email(client):
    for _ in range(5):
        assert client.post("/auth/otp/request", json={"email": "e@example.com"}).status_code == 202
    r = client.post("/auth/otp/request", json={"email": "e@example.com"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert client.post("/auth/otp/request", json={"email": "f@example.com"}).status_code == 202


def test_totp_attempts_rate_limited(user, client, outbox):
    client.post("/auth/otp/request", json={"email": user.email})
    step = client.post("/auth/otp/verify", json={"email": user.email, "code": last_code(outbox, user.email)}).json()
    codes = [
        client.post("/auth/totp/verify", json={"mfa_token": step["mfa_token"], "code": "000000"}).status_code
        for _ in range(11)
    ]
    assert codes[-1] == 429
