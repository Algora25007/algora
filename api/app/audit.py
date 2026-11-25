"""Append-only audit trail: every login and every create/update/delete is recorded."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from .models import AuditLog
from .ratelimit import client_ip


def record(
    db: Session,
    request: Request | None,
    *,
    action: str,
    user_id: uuid.UUID | None,
    entity_type: str | None = None,
    entity_id: Any = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """Add an audit row to the session; it commits with the change it describes."""
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            before=before,
            after=after,
            ip=client_ip(request) if request else None,
            user_agent=(request.headers.get("user-agent") or "")[:256] if request else None,
        )
    )
