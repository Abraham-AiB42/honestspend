"""Freeware money-in reminders: download CSV/OFX/statements — never bank passwords."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import AppSettings

VALID_CADENCE = frozenset({"off", "daily", "weekly", "monthly"})
VALID_FOCUS = frozenset({"transactions", "statements", "both"})

_CADENCE_DAYS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
}


def normalize_cadence(raw: str | None) -> str:
    c = (raw or "weekly").strip().lower()
    return c if c in VALID_CADENCE else "weekly"


def normalize_focus(raw: str | None) -> str:
    f = (raw or "transactions").strip().lower()
    return f if f in VALID_FOCUS else "transactions"


def mark_import_activity(session: Session, when: datetime | None = None) -> None:
    """Call after successful CSV/OFX/Plaid import so the reminder clock resets."""
    row = session.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1)
        session.add(row)
    row.import_last_at = when or datetime.now()
    # Clear snooze once they actually imported
    row.import_reminder_snooze_until = None
    session.flush()


def snooze_import_reminder(session: Session, *, days: int = 7, as_of: date | None = None) -> dict[str, Any]:
    row = session.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1)
        session.add(row)
    as_of = as_of or date.today()
    until = as_of + timedelta(days=max(1, int(days)))
    row.import_reminder_snooze_until = until
    session.flush()
    return {"ok": True, "snooze_until": until.isoformat()}


def build_import_reminder(
    session: Session,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Whether Home/digest should nudge the user to refresh books from their bank."""
    as_of = as_of or date.today()
    row = session.get(AppSettings, 1)
    cadence = normalize_cadence(getattr(row, "import_reminder_cadence", None) if row else None)
    focus = normalize_focus(getattr(row, "import_reminder_focus", None) if row else None)
    last = getattr(row, "import_last_at", None) if row else None
    snooze = getattr(row, "import_reminder_snooze_until", None) if row else None

    base: dict[str, Any] = {
        "cadence": cadence,
        "focus": focus,
        "due": False,
        "overdue_days": 0,
        "last_import_at": last.isoformat() if last else None,
        "snooze_until": snooze.isoformat() if snooze else None,
        "title": None,
        "reason": None,
        "action": "import",
        "button_label": "Import file",
        "freeware_hint": "CSV/OFX/PDF on this PC. Optional Plaid = your own keys. No paid bank feed.",
    }

    if cadence == "off":
        base["reason"] = "Reminders off — you update on your own schedule."
        return base

    if snooze and snooze >= as_of:
        base["reason"] = f"Snoozed until {snooze.isoformat()}."
        return base

    interval = _CADENCE_DAYS[cadence]
    if last is None:
        # Never imported: due once onboarding is done (caller can filter)
        overdue = interval  # treat as due
        base["due"] = True
        base["overdue_days"] = overdue
    else:
        last_d = last.date() if isinstance(last, datetime) else last
        days = (as_of - last_d).days
        base["overdue_days"] = max(0, days - interval)
        base["due"] = days >= interval

    if not base["due"]:
        next_due = None
        if last is not None:
            last_d = last.date() if isinstance(last, datetime) else last
            next_due = (last_d + timedelta(days=interval)).isoformat()
        base["next_due"] = next_due
        base["reason"] = "Books refresh on schedule."
        return base

    focus_copy = {
        "transactions": "Download a transactions CSV/OFX from your bank site, then Import.",
        "statements": "Download your latest account statement (CSV or PDF), then Import.",
        "both": "Download transactions and/or statements from your bank, then Import.",
    }[focus]

    if last is None:
        title = "Bring in bank activity"
        reason = (
            f"{focus_copy} "
            "LedgerRing is free and local — we never store bank passwords. "
            f"You chose {cadence} reminders."
        )
    else:
        age = base["overdue_days"]
        title = {
            "daily": "Daily books refresh",
            "weekly": "Weekly bank export",
            "monthly": "Monthly statement / export",
        }.get(cadence, "Update books from your bank")
        reason = (
            f"Last import was {interval + age} day(s) ago (your cadence: {cadence}). "
            f"{focus_copy}"
        )

    base["title"] = title
    base["reason"] = reason
    base["button_label"] = "Import CSV / OFX"
    return base
