"""Request and response bodies."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar

from algora_engine.browser_import import BROKER_IDS
from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
Code6 = Annotated[str, Field(pattern=r"^\d{6}$")]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----- auth -----------------------------------------------------------------------------------------------------


class OtpRequest(_In):
    email: EmailStr


class OtpVerify(_In):
    email: EmailStr
    code: Code6


class MfaStep(BaseModel):
    """Returned after the email code. ``stage`` says whether to enrol an authenticator or enter its code."""

    stage: Literal["totp_enroll", "totp_verify"]
    mfa_token: str
    totp_secret: str | None = None
    otpauth_uri: str | None = None


class TotpSubmit(_In):
    mfa_token: str = Field(min_length=10, max_length=2048)
    code: Code6


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int


class RefreshIn(_In):
    refresh_token: str = Field(min_length=20, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    created_at: datetime
    last_login_at: datetime | None
    totp_enabled: bool


# ----- broker accounts ------------------------------------------------------------------------------------------


def _clean_label(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("label must not be blank")
    if any(ord(c) < 32 for c in v):
        raise ValueError("label must not contain control characters")
    return v


Label = Annotated[str, Field(min_length=1, max_length=40), AfterValidator(_clean_label)]


class AccountIn(_In):
    broker_id: str
    label: Label

    @field_validator("broker_id")
    @classmethod
    def _broker(cls, v: str) -> str:
        if v not in BROKER_IDS:
            raise ValueError("unknown broker")
        return v


class AccountPatch(_In):
    label: Label


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    broker_id: str
    label: str
    created_at: datetime
    updated_at: datetime


# ----- strategies -----------------------------------------------------------------------------------------------


class StrategyCreate(_In):
    spec: dict[str, Any]
    broker_account_id: uuid.UUID | None = None
    paused: bool = False


class StrategyUpdate(_In):
    """Change the rules: creates a new immutable version."""

    spec: dict[str, Any]


class StrategyPatch(_In):
    """Change settings that are not part of the rules (no new version)."""

    paused: bool | None = None
    broker_account_id: uuid.UUID | None = None
    clear_broker_account: bool = False


class StrategyOut(BaseModel):
    id: uuid.UUID
    name: str
    template: str
    paused: bool
    broker_account_id: uuid.UUID | None
    current_version: int
    spec: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class StrategyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: int
    schema_version: int
    spec: dict[str, Any]
    created_at: datetime


# ----- risk -----------------------------------------------------------------------------------------------------


class RiskIn(_In):
    daily_loss_limit: Annotated[Decimal | None, Field(ge=0, le=Decimal("100000000"), decimal_places=2)] = None
    max_trades_per_day: Annotated[int | None, Field(ge=1, le=500)] = None
    stop_new_entries_after: str | None = None
    max_orders_per_second: Annotated[int, Field(ge=1, le=10)] = 10
    kill_switch: bool = False

    @field_validator("stop_new_entries_after")
    @classmethod
    def _time(cls, v: str | None) -> str | None:
        if v is not None and not TIME_RE.match(v):
            raise ValueError("time must be HH:MM")
        return v


class RiskOut(RiskIn):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    updated_at: datetime | None = None


# ----- backtests ------------------------------------------------------------------------------------------------


class BacktestListItem(BaseModel):
    id: uuid.UUID
    strategy_id: uuid.UUID | None
    strategy_version: int | None
    name: str
    candles_meta: dict[str, Any]
    summary: dict[str, Any]
    created_at: datetime


class BacktestOut(BacktestListItem):
    spec: dict[str, Any]
    config: dict[str, Any]
    equity_curve: list[Any]
    per_day: list[Any]
    trades: list[Any]
    warnings: list[str]
    engine_version: str


T = TypeVar("T")


class Page(BaseModel, Generic[T]):  # noqa: UP046 - explicit TypeVar keeps pydantic generics simple
    items: list[T]
    total: int
    limit: int
    offset: int


# ----- import ---------------------------------------------------------------------------------------------------


class ImportedStrategy(BaseModel):
    """How one browser bot was mapped to an engine template."""

    id: uuid.UUID
    name: str
    browser_strategy: str
    template: str
    defaults_used: dict[str, Any] = Field(description="Template settings the bot did not have, filled with defaults")
    notes: list[str] = Field(description="How the browser settings were interpreted")


class ImportResult(BaseModel):
    accounts_created: int
    accounts_existing: int
    strategies_created: int
    strategies_created_details: list[ImportedStrategy] = Field(default_factory=list)
    strategies_skipped: list[dict[str, str]]
    risk_imported: bool
    backtest_settings_imported: bool


# ----- templates ------------------------------------------------------------------------------------------------


class TemplateSpecIn(_In):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    instrument: dict[str, Any] = Field(description="DSL instrument: symbol, exchange, segment[, lot_size, tick_size]")
    params: dict[str, Any] = Field(default_factory=dict, description="Template parameters; omitted ones use defaults")


class TemplateSpecOut(BaseModel):
    template: str
    spec: dict[str, Any]
    params_used: dict[str, Any] = Field(description="Every parameter after defaults were applied")
    defaults_used: list[str] = Field(description="Parameters that were not sent and took their default")
