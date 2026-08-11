"""Resumable smart setup wizard — phase state machine (PR1 foundation).

Later PRs fill phase bodies (Plaid, cash loop, discover, budgets). This module
owns phase transitions, payload, and needs_setup.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, AppSettings, ScheduledItem

# Ordered phases for progress UI (path-specific subsets applied in get_state)
ALL_PHASES: list[dict[str, str]] = [
    {"id": "welcome", "title": "Welcome", "blurb": "What we’ll build together"},
    {"id": "path", "title": "How to connect money", "blurb": "Plaid, CSV, or quick manual"},
    {"id": "plaid_keys", "title": "Plaid keys", "blurb": "Your client id + secret (local only)"},
    {"id": "plaid_link", "title": "Link banks", "blurb": "Connect institutions (trial ≤10 Items)"},
    {"id": "ai_keys", "title": "AI helpers (optional)", "blurb": "Grok or other LLM keys you prefer"},
    {"id": "cash_loop", "title": "Cash accounts", "blurb": "Add checking, savings, money market"},
    {"id": "import_cash", "title": "Import history", "blurb": "Bank CSV/OFX per account"},
    {"id": "discover", "title": "Find cards & bills", "blurb": "Skim debits for liabilities"},
    {"id": "liabilities", "title": "Set up debts", "blurb": "Cards, loans, payment plans"},
    {"id": "recurring", "title": "Recurring", "blurb": "Bills, subscriptions, investments"},
    {"id": "categorize", "title": "Categories", "blurb": "Confirm spend categories"},
    {"id": "budgets", "title": "Budgets", "blurb": "Plans from your history"},
    {"id": "buffers", "title": "Safety buffers", "blurb": "Per-account + total cash floor"},
    {"id": "manual", "title": "Quick setup", "blurb": "Manual cash + card + bill"},
    {"id": "done", "title": "Done", "blurb": "You’re ready for Home"},
]

PHASE_ORDER = [p["id"] for p in ALL_PHASES]

# Default linear path after path choice
PATH_PHASES: dict[str, list[str]] = {
    "plaid": [
        "welcome",
        "path",
        "plaid_keys",
        "plaid_link",
        "ai_keys",
        "discover",
        "liabilities",
        "recurring",
        "categorize",
        "budgets",
        "buffers",
        "done",
    ],
    "csv": [
        "welcome",
        "path",
        "cash_loop",
        "import_cash",
        "discover",
        "liabilities",
        "recurring",
        "categorize",
        "budgets",
        "buffers",
        "done",
    ],
    "manual": ["welcome", "path", "manual", "done"],
}

VALID_PATHS = frozenset({"plaid", "csv", "manual"})


def _settings(session: Session) -> AppSettings:
    row = session.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1)
        session.add(row)
        session.flush()
    return row


def _payload(settings: AppSettings) -> dict[str, Any]:
    raw = getattr(settings, "setup_payload_json", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_payload(settings: AppSettings, data: dict[str, Any]) -> None:
    settings.setup_payload_json = json.dumps(data)


def _phase(settings: AppSettings) -> str:
    p = (getattr(settings, "setup_phase", None) or "welcome").strip().lower()
    if p not in PHASE_ORDER:
        return "welcome"
    return p


def _path(settings: AppSettings) -> str | None:
    p = getattr(settings, "setup_path", None)
    if p and str(p).lower() in VALID_PATHS:
        return str(p).lower()
    return None


def _checklist(session: Session) -> dict[str, Any]:
    accounts = session.query(Account).all()
    cash = [
        a
        for a in accounts
        if a.is_cash_for_ifpp or a.kind in ("checking", "savings", "cash")
    ]
    credit = [a for a in accounts if a.kind == "credit"]
    loans = [a for a in accounts if a.kind == "loan"]
    bills = (
        session.query(ScheduledItem)
        .filter(ScheduledItem.active.is_(True), ScheduledItem.kind == "expense")
        .count()
    )
    return {
        "cash_accounts": len(cash),
        "credit_accounts": len(credit),
        "loan_accounts": len(loans),
        "active_bills": int(bills),
        "total_accounts": len(accounts),
        "has_cash": len(cash) > 0,
    }


def _path_phases(path: str | None) -> list[str]:
    if path and path in PATH_PHASES:
        return list(PATH_PHASES[path])
    # Before path choice: welcome → path only
    return ["welcome", "path"]


def get_setup_state(session: Session) -> dict[str, Any]:
    settings = _settings(session)
    phase = _phase(settings)
    path = _path(settings)
    # Already completed via old onboarding
    if bool(getattr(settings, "onboarding_complete", False)) and phase != "done":
        phase = "done"
        settings.setup_phase = "done"
        session.flush()

    seq = _path_phases(path)
    # If current phase not in seq (e.g. switched path), clamp
    if phase not in seq and phase != "done":
        if phase in PHASE_ORDER and path:
            # keep phase if still valid globally
            pass
        elif "welcome" in seq:
            phase = "welcome"

    try:
        idx = seq.index(phase) if phase in seq else 0
    except ValueError:
        idx = 0
    total = max(1, len(seq) - 1)  # exclude counting only
    progress = 100 if phase == "done" else int(round(100 * idx / total))

    phase_meta = next((p for p in ALL_PHASES if p["id"] == phase), ALL_PHASES[0])
    steps = []
    for pid in seq:
        meta = next((p for p in ALL_PHASES if p["id"] == pid), {"id": pid, "title": pid, "blurb": ""})
        steps.append(
            {
                **meta,
                "current": pid == phase,
                "done": seq.index(pid) < idx if pid in seq else False,
            }
        )

    complete = phase == "done" or bool(getattr(settings, "onboarding_complete", False))
    checklist = _checklist(session)
    # needs_setup: wizard not finished
    needs_setup = phase != "done" and not bool(getattr(settings, "onboarding_complete", False))

    return {
        "phase": phase,
        "path": path,
        "progress_pct": progress,
        "phase_title": phase_meta.get("title"),
        "phase_blurb": phase_meta.get("blurb"),
        "steps": steps,
        "payload": _payload(settings),
        "checklist": checklist,
        "needs_setup": needs_setup,
        "onboarding_complete": complete,
        "can_resume": needs_setup and phase not in ("welcome",),
        "product_name": getattr(settings, "product_name", None) or "HonestSpend",
        "plaid_item_limit": 10,  # trial Item cap (institutions)
        "hints": {
            "welcome": "About 15–40 minutes. You can pause anytime — we’ll remember where you left off.",
            "path": "Plaid uses your free trial keys (up to 10 bank connections). CSV never needs bank passwords in our app.",
            "manual": "Fast path: one checking, optional card and bill. You can import later.",
        },
    }


def set_phase(
    session: Session,
    phase: str,
    *,
    path: str | None = None,
    merge_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = _settings(session)
    phase = (phase or "").strip().lower()
    if phase not in PHASE_ORDER:
        raise ValueError(f"Unknown phase: {phase}")
    if path is not None:
        path_l = path.strip().lower()
        if path_l not in VALID_PATHS:
            raise ValueError("path must be plaid, csv, or manual")
        settings.setup_path = path_l
    if merge_payload:
        data = _payload(settings)
        data.update(merge_payload)
        _save_payload(settings, data)
    settings.setup_phase = phase
    if phase == "done":
        settings.onboarding_complete = True
    session.flush()
    return get_setup_state(session)


def advance_setup(
    session: Session,
    *,
    action: str = "next",
    path: str | None = None,
    payload: dict[str, Any] | None = None,
    target_phase: str | None = None,
) -> dict[str, Any]:
    """Advance, skip, or jump phases.

    action: next | back | skip_phase | jump | complete | set_path
    """
    settings = _settings(session)
    phase = _phase(settings)
    cur_path = _path(settings)
    action = (action or "next").strip().lower()

    if payload:
        data = _payload(settings)
        data.update(payload)
        _save_payload(settings, data)

    if action == "complete" or target_phase == "done":
        return set_phase(session, "done", merge_payload=payload)

    if action == "set_path" or (action == "next" and phase == "path"):
        chosen = (path or (payload or {}).get("path") or cur_path or "").strip().lower()
        if chosen not in VALID_PATHS:
            raise ValueError("Choose path: plaid, csv, or manual")
        settings.setup_path = chosen
        # First phase after path
        nxt = {
            "plaid": "plaid_keys",
            "csv": "cash_loop",
            "manual": "manual",
        }[chosen]
        settings.setup_phase = nxt
        session.flush()
        return get_setup_state(session)

    if action == "jump" and target_phase:
        return set_phase(session, target_phase, path=path, merge_payload=payload)

    seq = _path_phases(cur_path if phase != "path" else cur_path)
    if phase == "welcome" and action == "next":
        settings.setup_phase = "path"
        session.flush()
        return get_setup_state(session)

    if phase not in seq:
        seq = _path_phases(cur_path)

    try:
        idx = seq.index(phase)
    except ValueError:
        idx = 0

    if action == "back":
        new_idx = max(0, idx - 1)
        settings.setup_phase = seq[new_idx]
        session.flush()
        return get_setup_state(session)

    if action in ("next", "skip_phase"):
        new_idx = min(len(seq) - 1, idx + 1)
        settings.setup_phase = seq[new_idx]
        if settings.setup_phase == "done":
            settings.onboarding_complete = True
        session.flush()
        return get_setup_state(session)

    raise ValueError(f"Unknown action: {action}")


def mark_setup_done(session: Session, *, note: str | None = None) -> dict[str, Any]:
    """Force complete (skip wizard)."""
    settings = _settings(session)
    settings.setup_phase = "done"
    settings.onboarding_complete = True
    if note:
        data = _payload(settings)
        data["skip_note"] = note
        _save_payload(settings, data)
    session.flush()
    try:
        from financial_os.services.backup import create_backup

        create_backup(as_zip=True, note=note or "setup-done")
    except Exception:
        pass
    return get_setup_state(session)


def sync_legacy_complete(session: Session) -> None:
    """Call after apply_first_run / quick_setup so wizard matches."""
    settings = _settings(session)
    settings.setup_phase = "done"
    settings.onboarding_complete = True
    if not settings.setup_path:
        settings.setup_path = "manual"
    session.flush()
