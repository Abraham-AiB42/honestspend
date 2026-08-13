"""Resumable smart setup wizard — phase state machine (PR1 foundation).

Later PRs fill phase bodies (Plaid, cash loop, discover, budgets). This module
owns phase transitions, payload, and needs_setup.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, AppSettings, ScheduledItem

# Ordered phases for progress UI (path-specific subsets applied in get_state)
ALL_PHASES: list[dict[str, str]] = [
    {"id": "welcome", "title": "Welcome", "blurb": "What can you safely spend?"},
    {"id": "storage", "title": "Where books live", "blurb": "This PC or your cloud folder"},
    {"id": "security", "title": "App lock", "blurb": "None, PIN, password, or device unlock"},
    {"id": "path", "title": "Connect money", "blurb": "Fast balances, CSV, or Plaid"},
    {"id": "manual", "title": "Quick balances", "blurb": "2 minutes to Safe to spend"},
    {"id": "plaid_keys", "title": "Plaid keys", "blurb": "Your client id + secret (local only)"},
    {"id": "plaid_link", "title": "Link banks", "blurb": "Connect institutions (trial ≤10 Items)"},
    {"id": "cash_loop", "title": "Cash + import", "blurb": "Add accounts and CSV/OFX"},
    {"id": "import_cash", "title": "Import history", "blurb": "Bank CSV/OFX per account"},
    {"id": "power_menu", "title": "Make it smarter", "blurb": "Optional — bills, categories, budgets"},
    {"id": "ai_keys", "title": "AI helpers", "blurb": "Optional LLM keys"},
    {"id": "discover", "title": "Find cards & bills", "blurb": "Skim debits for liabilities"},
    {"id": "liabilities", "title": "Set up debts", "blurb": "Cards, loans, payment plans"},
    {"id": "recurring", "title": "Recurring", "blurb": "Bills, subscriptions, investments"},
    {"id": "categorize", "title": "Categories", "blurb": "Confirm spend categories"},
    {"id": "budgets", "title": "Budgets", "blurb": "Plans from your history"},
    {"id": "buffers", "title": "Safety buffers", "blurb": "Per-account + total cash floor"},
    {"id": "done", "title": "Done", "blurb": "You’re ready for Home"},
]

PHASE_ORDER = [p["id"] for p in ALL_PHASES]

# Storage + security first, then money path, then optional power_menu
PATH_PHASES: dict[str, list[str]] = {
    "manual": ["welcome", "storage", "security", "path", "manual", "power_menu", "done"],
    "csv": ["welcome", "storage", "security", "path", "cash_loop", "power_menu", "done"],
    "plaid": [
        "welcome",
        "storage",
        "security",
        "path",
        "plaid_keys",
        "plaid_link",
        "power_menu",
        "done",
    ],
}

# Depth modules reachable from power_menu (jump only within this set + done)
POWER_DEPTH = ("discover", "recurring", "categorize", "budgets", "buffers", "ai_keys")

# Welcome / storage / security must be completed — no Skip this step
REQUIRED_EARLY_PHASES = frozenset({"welcome", "storage", "security"})

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
    # Before path choice: welcome → storage → security → path
    return ["welcome", "storage", "security", "path"]


def get_setup_state(session: Session) -> dict[str, Any]:
    """Read-only setup status (no DB writes — GET must stay pure)."""
    settings = _settings(session)
    phase = _phase(settings)
    path = _path(settings)
    # Reflect legacy complete without mutating (migration / write paths set phase=done)
    if bool(getattr(settings, "onboarding_complete", False)) and phase != "done":
        phase = "done"

    seq = _path_phases(path)
    in_depth = phase in POWER_DEPTH
    # Clamp orphan phases onto path sequence (keep optional depth modules)
    if phase not in seq and phase != "done" and not in_depth:
        if phase == "liabilities":
            phase = "discover"
            in_depth = True
        elif phase not in PHASE_ORDER:
            phase = "welcome"

    # Progress: depth modules sit under power_menu index
    display_phase = "power_menu" if in_depth and "power_menu" in seq else phase
    try:
        idx = seq.index(display_phase) if display_phase in seq else 0
    except ValueError:
        idx = 0
    total = max(1, len(seq) - 1)
    progress = 100 if phase == "done" else int(round(100 * idx / total))

    phase_meta = next((p for p in ALL_PHASES if p["id"] == phase), ALL_PHASES[0])
    steps = []
    for sid in seq:
        meta = next((p for p in ALL_PHASES if p["id"] == sid), {"id": sid, "title": sid, "blurb": ""})
        steps.append(
            {
                **meta,
                "current": sid == display_phase,
                "done": seq.index(sid) < idx if sid in seq else False,
            }
        )

    complete = phase == "done" or bool(getattr(settings, "onboarding_complete", False))
    checklist = _checklist(session)
    needs_setup = phase != "done" and not bool(getattr(settings, "onboarding_complete", False))
    can_complete = bool(checklist.get("has_cash"))

    power_modules = [
        {
            **next(
                (p for p in ALL_PHASES if p["id"] == mid),
                {"id": mid, "title": mid, "blurb": ""},
            )
        }
        for mid in POWER_DEPTH
    ]

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
        "can_complete": can_complete,
        "can_resume": needs_setup and phase not in ("welcome",),
        "product_name": getattr(settings, "product_name", None) or "HonestSpend",
        "plaid_item_limit": 10,
        "power_modules": power_modules,
        "in_power_depth": in_depth,
        "hints": {
            "welcome": "About 2 minutes to a Safe-to-spend number. First: where books live + optional app lock.",
            "storage": "Local is simplest. A cloud folder lets another PC of yours open the same books — one writer at a time.",
            "security": "Optional app lock (PIN, password, or Windows Hello) with AES-256 at rest. Forget the secret and sealed books cannot be recovered.",
            "path": "Plaid uses your free trial keys (up to 10 bank connections). CSV never needs bank passwords in our app.",
            "manual": "Fast path: one checking, optional card and bill — then optional smarter setup.",
            "power_menu": "You already have a number. Optionally find bills, categorize, budget, or set AI keys.",
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
    if phase == "done":
        force = bool((merge_payload or {}).get("force_empty"))
        return mark_setup_done(
            session,
            note=(merge_payload or {}).get("note"),
            force_empty=force,
        )
    settings.setup_phase = phase
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
        force = bool((payload or {}).get("force_empty") or (payload or {}).get("force"))
        return mark_setup_done(session, note=(payload or {}).get("note"), force_empty=force)

    if action == "skip_phase" and phase in REQUIRED_EARLY_PHASES:
        return get_setup_state(session)

    if action == "set_path" or (action == "next" and phase == "path"):
        chosen = (path or (payload or {}).get("path") or cur_path or "").strip().lower()
        if chosen not in VALID_PATHS:
            raise ValueError("Choose path: plaid, csv, or manual")
        settings.setup_path = chosen
        nxt = {
            "plaid": "plaid_keys",
            "csv": "cash_loop",
            "manual": "manual",
        }[chosen]
        settings.setup_phase = nxt
        session.flush()
        return get_setup_state(session)

    seq = _path_phases(cur_path)
    if phase == "welcome" and action == "next":
        settings.setup_phase = "storage"
        session.flush()
        return get_setup_state(session)

    # Map removed phases
    if phase == "liabilities":
        phase = "discover" if "discover" in seq else (seq[0] if seq else "welcome")
        settings.setup_phase = phase
        session.flush()

    if phase not in seq and phase != "done" and phase not in POWER_DEPTH:
        seq = _path_phases(cur_path)
        phase = seq[0] if seq else "welcome"

    if action == "jump" and target_phase:
        tp = (target_phase or "").strip().lower()
        if tp == "done":
            force = bool((payload or {}).get("force_empty") or (payload or {}).get("force"))
            return mark_setup_done(session, note=(payload or {}).get("note"), force_empty=force)
        # power_menu can jump into optional depth modules
        allowed = set(seq) | set(POWER_DEPTH) | {"power_menu", "welcome", "path"}
        if tp not in allowed:
            raise ValueError(f"Cannot jump to {tp} on path {cur_path or 'unset'}")
        return set_phase(session, tp, path=path, merge_payload=payload)

    # From optional depth, next/skip/back return to power_menu (not forced tunnel)
    if phase in POWER_DEPTH and action in ("next", "skip_phase", "back"):
        settings.setup_phase = "power_menu"
        session.flush()
        return get_setup_state(session)

    try:
        idx = seq.index(phase) if phase in seq else 0
    except ValueError:
        idx = 0

    if action == "back":
        new_idx = max(0, idx - 1)
        settings.setup_phase = seq[new_idx]
        session.flush()
        return get_setup_state(session)

    # power_menu "next" without jump → finish when cash exists
    if action == "next" and phase == "power_menu":
        return mark_setup_done(session, note=(payload or {}).get("note"), force_empty=False)

    if action in ("next", "skip_phase"):
        # skip_phase never auto-completes without cash
        new_idx = min(len(seq) - 1, idx + 1)
        next_phase = seq[new_idx]
        if next_phase == "done":
            force = action == "skip_phase"  # skipping into done still needs cash unless force
            if action == "skip_phase":
                # Stay on last real phase — use complete action for finish
                return get_setup_state(session)
            return mark_setup_done(session, note=None, force_empty=False)
        settings.setup_phase = next_phase
        session.flush()
        return get_setup_state(session)

    raise ValueError(f"Unknown action: {action}")


def mark_setup_done(
    session: Session,
    *,
    note: str | None = None,
    force_empty: bool = False,
) -> dict[str, Any]:
    """Mark wizard complete. Requires cash unless force_empty (explicit abandon)."""
    settings = _settings(session)
    checklist = _checklist(session)
    if not checklist.get("has_cash") and not force_empty:
        raise ValueError(
            "Add at least one cash account before finishing setup. "
            "Or pass force_empty=true to leave books incomplete."
        )
    settings.setup_phase = "done"
    settings.onboarding_complete = True
    if note or force_empty:
        data = _payload(settings)
        if note:
            data["skip_note"] = note
        if force_empty:
            data["force_empty"] = True
        _save_payload(settings, data)
    session.flush()
    try:
        from honestspend.services.backup import create_backup

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
