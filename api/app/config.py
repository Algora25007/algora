"""Runtime settings, read from ``ALGORA_*`` environment variables (or a local ``.env``)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GITHUB_PAGES_ORIGIN = "https://algora25007.github.io"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALGORA_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://algora:algora@localhost:5432/algora"
    redis_url: str | None = None

    #: Signs JWTs and peppers stored hashes (OTP codes, refresh tokens). At least 32 characters.
    secret_key: SecretStr = Field(min_length=32)
    #: Fernet key (urlsafe base64, 32 bytes) used to encrypt TOTP secrets at rest.
    totp_encryption_key: SecretStr
    jwt_issuer: str = "algora-api"
    access_ttl_minutes: int = Field(default=15, ge=1, le=60)
    refresh_ttl_days: int = Field(default=30, ge=1, le=90)
    mfa_ttl_minutes: int = Field(default=5, ge=1, le=15)
    otp_ttl_minutes: int = Field(default=10, ge=1, le=30)
    otp_max_attempts: int = Field(default=5, ge=1, le=10)
    totp_issuer: str = "Algora"

    email_backend: Literal["console", "smtp"] = "console"
    email_from: str = "Algora <no-reply@algora.local>"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True

    cors_origins: list[str] = Field(default_factory=lambda: [GITHUB_PAGES_ORIGIN])
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    #: Only trust X-Forwarded-For when the API sits behind a proxy you control.
    trust_forwarded_for: bool = False

    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    max_candles: int = Field(default=500_000, ge=100)
    max_import_bytes: int = Field(default=1024 * 1024, ge=1024)

    @model_validator(mode="after")
    def _prod_safety(self) -> Settings:
        if self.env == "prod":
            if self.email_backend == "console":
                raise ValueError("ALGORA_EMAIL_BACKEND=console is not allowed in prod")
            if self.rate_limit_backend == "memory":
                raise ValueError("use the redis rate limiter in prod (several API workers)")
        if self.email_backend == "smtp" and not self.smtp_host:
            raise ValueError("ALGORA_SMTP_HOST is required for the smtp email backend")
        if self.rate_limit_backend == "redis" and not self.redis_url:
            raise ValueError("ALGORA_REDIS_URL is required for the redis rate limiter")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
