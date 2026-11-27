"""strategy DSL schema v2: upgrade stored strategy versions from schema v1

Schema v2 is a strict superset of v1 (new optional fields and operand kinds whose defaults reproduce v1), so a v1
document becomes v2 by changing ``schema_version`` alone; its rules and results are unchanged. Backtest runs keep
the spec exactly as it was run (they are a record).

Downgrade turns v2 documents back into v1 only when they use nothing v1 lacked; otherwise it refuses, rather than
silently dropping rules.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-28 00:30:00
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V1_REFS = {"first_candle_open", "first_candle_high", "first_candle_low", "first_candle_close", "day_open"}
V1_TEMPLATES = {"first_candle_cross", "custom"}


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE strategy_versions SET schema_version = 2, "
            "spec = jsonb_set(spec, '{schema_version}', '2'::jsonb) WHERE schema_version = 1"
        )
    )


def _v1_operand(o: Any) -> bool:
    if not isinstance(o, dict) or o.get("kind") not in ("price", "const", "session"):
        return False
    if o["kind"] == "session":
        return o.get("ref") in V1_REFS and not o.get("offset_points")
    return True


def v1_compatible(doc: dict) -> bool:
    """True when a v2 document uses only what schema v1 had (so it can be stored as v1 without loss)."""
    if doc.get("template", "custom") not in V1_TEMPLATES:
        return False
    session = doc.get("session") or {}
    if session.get("opening_range_minutes") is not None:
        return False
    if session.get("signals_from", "after_opening_range") != "after_opening_range":
        return False
    exits = doc.get("exits") or {}
    for k in ("stop_level", "target_level", "target_r_multiple", "target_range_multiple", "conditions"):
        if exits.get(k) is not None:
            return False
    entry = doc.get("entry") or {}
    for c in [*entry.get("long", []), *entry.get("short", [])]:
        if c.get("op") == "time_between" or not (_v1_operand(c.get("left")) and _v1_operand(c.get("right"))):
            return False
    return True


def _strip_v2_defaults(doc: dict) -> dict:
    out = json.loads(json.dumps(doc))
    out["schema_version"] = 1
    session = out.get("session")
    if isinstance(session, dict):
        session.pop("opening_range_minutes", None)
        session.pop("signals_from", None)
    exits = out.get("exits")
    if isinstance(exits, dict):
        for k in ("stop_level", "target_level", "target_r_multiple", "target_range_multiple", "conditions"):
            exits.pop(k, None)
    entry = out.get("entry") or {}
    for c in [*entry.get("long", []), *entry.get("short", [])]:
        for side in ("left", "right"):
            if isinstance(c.get(side), dict):
                c[side].pop("offset_points", None)
    return out


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, spec FROM strategy_versions WHERE schema_version = 2")).all()
    blocked = [str(r.id) for r in rows if not v1_compatible(r.spec)]
    if blocked:
        raise RuntimeError(
            f"{len(blocked)} strategy version(s) use schema v2 features and cannot be stored as v1, e.g. {blocked[0]}"
        )
    for r in rows:
        conn.execute(
            sa.text("UPDATE strategy_versions SET schema_version = 1, spec = CAST(:spec AS jsonb) WHERE id = :id"),
            {"spec": json.dumps(_strip_v2_defaults(r.spec)), "id": r.id},
        )
