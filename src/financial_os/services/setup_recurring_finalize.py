"""Setup wizard: remaining recurring suggestions after discover."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Profile, ScheduledItem
from financial_os.services.recurring_detect import accept_recurring_suggestion, detect_recurring


def _profile_id(session: Session, profile_id: int | None) -> int | None:
    if profile_id is not None:
        return int(profile_id)
    d = session.query(Profile).filter(Profile.is_default.is_(True)).first()
    if d:
        return int(d.id)
    p = session.query(Profile).filter(Profile.slug == "personal").first()
    return int(p.id) if p else None


def remaining_recurring(
    session: Session,
    *,
    profile_id: int | None = None,
    lookback_days: int = 90,
    limit: int = 15,
) -> dict[str, Any]:
    pid = _profile_id(session, profile_id)
    det = detect_recurring(
        session,
        profile_id=pid,
        lookback_days=lookback_days,
        min_occurrences=2,
        limit=limit,
    )
    active = (
        session.query(ScheduledItem)
        .filter(ScheduledItem.active.is_(True))
        .filter(*([ScheduledItem.profile_id == pid] if pid is not None else []))
        .count()
    )
    return {
        "profile_id": pid,
        "active_bills": int(active),
        "suggestions": det.get("suggestions") or [],
        "count": det.get("count") or 0,
        "title": det.get("title"),
        "message": (
            f"{det.get('count') or 0} more possible recurring bill(s). "
            "Accept the real ones — dismiss the rest."
            if det.get("count")
            else "No additional recurring patterns — you're set for bills."
        ),
    }


def apply_recurring_choices(
    session: Session,
    *,
    accepted: list[dict[str, Any]],
    profile_id: int | None = None,
) -> dict[str, Any]:
    """Accept selected suggestions (body rows from detect_recurring)."""
    pid = _profile_id(session, profile_id)
    created = []
    for item in accepted:
        if item.get("selected") is False:
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        amt = item.get("amount_abs") or item.get("amount") or "0"
        try:
            # amount from detect may be negative string
            from decimal import Decimal

            a = abs(Decimal(str(amt)))
        except Exception:
            continue
        if a <= 0:
            continue
        # Prefer explicit account_id / suggested_account_id (card-first: charge on card)
        raw_aid = item.get("account_id")
        if raw_aid is None:
            raw_aid = item.get("suggested_account_id")
        try:
            aid = int(raw_aid) if raw_aid is not None and str(raw_aid).strip() != "" else None
        except (TypeError, ValueError):
            aid = None
        res = accept_recurring_suggestion(
            session,
            name=name,
            amount=a,
            cadence=item.get("cadence") or "monthly",
            next_date=item.get("suggested_next_date"),
            profile_id=pid,
            account_id=aid,
        )
        created.append(res)
    session.flush()
    return {
        "ok": True,
        "accepted": len(created),
        "results": created,
        "remaining": remaining_recurring(session, profile_id=pid),
        "message": f"Added or updated {len(created)} recurring bill(s).",
    }
