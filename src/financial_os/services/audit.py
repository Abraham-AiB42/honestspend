"""Lightweight local audit log for multi-user accountability."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import AuditEvent


def log_event(
    session: Session,
    *,
    username: str,
    role: str,
    action: str,
    path: str | None = None,
    detail: str | None = None,
) -> None:
    try:
        session.add(
            AuditEvent(
                username=username[:64],
                role=role[:32],
                action=action[:64],
                path=(path or "")[:256] or None,
                detail=(detail or "")[:512] or None,
            )
        )
        session.flush()
    except Exception:
        # Never break the request on audit failure
        pass


def list_events(session: Session, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        session.query(AuditEvent)
        .order_by(AuditEvent.id.desc())
        .limit(min(500, max(1, limit)))
        .all()
    )
    return [
        {
            "id": r.id,
            "username": r.username,
            "role": r.role,
            "action": r.action,
            "path": r.path,
            "detail": r.detail,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
