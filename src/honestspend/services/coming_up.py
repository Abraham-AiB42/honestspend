"""Coming up strip: cash-side schedules until calendar days or next paydays.

Simple Home list of outflows (and optional inflows) through either N calendar
days (7–14) or the next 1–2 paychecks. Skips credit-account schedules so card
bills are not double-counted; includes Card payment · … on cash.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.orm import Session

from honestspend.db import Account, AppSettings, ScheduledItem
from honestspend.engine.schedule_expand import expand_scheduled
from honestspend.services.ifpp_service import (
    _active_accounts,
    _is_credit_account_schedule,
    _resolve_scope_and_profile,
)

WindowMode = Literal["auto", "calendar", "paydays"]

ZERO = Decimal("0")
CENT = Decimal("0.01")
MAX_PAYDAY_HORIZON_DAYS = 45
EMPTY_HINT = "Nothing scheduled in this window — add a bill or paycheck in Add"


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return _d(v).quantize(CENT)


def is_income_schedule(item: ScheduledItem) -> bool:
    """True if kind==income or amount > 0. owner_draw is never income (even if amount > 0)."""
    kind = (item.kind or "").lower()
    if kind == "owner_draw":
        return False
    if kind == "income":
        return True
    return _d(item.amount) > ZERO


def _clamp_calendar_days(n: int) -> int:
    return max(7, min(14, int(n)))


def _clamp_payday_count(n: int) -> int:
    return max(1, min(2, int(n)))


def _label_date(d: date) -> str:
    """Plain English short date: 'Fri Mar 6'."""
    return f"{d.strftime('%a')} {d.strftime('%b')} {d.day}"


def next_income_dates(
    session: Session,
    *,
    as_of: date,
    profile_id: int | None,
    scope: str,
    count: int = 2,
    max_days: int = 45,
) -> list[date]:
    """Next N income occurrence dates from active income schedules in scope.

    Includes all active income (any account). Dates are already limited to
    [as_of, as_of+max_days] so callers need not re-cap by date.
    """
    count = max(1, int(count))
    max_days = max(1, int(max_days))
    horizon_end = as_of + timedelta(days=max_days)

    sc = scope if scope in ("entity", "group") else "entity"
    sched_q = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if sc == "entity" and profile_id is not None:
        sched_q = sched_q.filter(ScheduledItem.profile_id == profile_id)

    dates: set[date] = set()
    for s in sched_q.all():
        if not is_income_schedule(s):
            continue
        amt = _d(s.amount)
        # Expand with positive amount so occurrences are generated even if stored negative with kind=income
        if amt <= ZERO:
            amt = abs(amt) if amt != ZERO else Decimal("1")
        occ = expand_scheduled(
            item_id=s.id,
            name=s.name or "Income",
            amount=amt,
            next_date=s.next_date,
            cadence=s.cadence or "monthly",
            certainty=s.certainty or "fixed",
            as_of=as_of,
            horizon_end=horizon_end,
            end_date=getattr(s, "end_date", None),
            start_date=getattr(s, "start_date", None),
            active=bool(s.active),
        )
        for ev in occ:
            if as_of <= ev.on_date <= horizon_end:
                dates.add(ev.on_date)

    return sorted(dates)[:count]


def _calendar_window(
    *,
    as_of: date,
    calendar_days: int,
    window_fallback: str | None = None,
) -> dict[str, Any]:
    end = as_of + timedelta(days=calendar_days)
    if window_fallback == "no_income":
        label = f"Next {calendar_days} days (no paycheck scheduled)"
    else:
        label = f"Next {calendar_days} days"
    return {
        "mode_effective": "calendar",
        "window_start": as_of.isoformat(),
        "window_end": end.isoformat(),
        "calendar_days": calendar_days,
        "payday_count": None,
        "paydays": [],
        "label": label,
        "window_fallback": window_fallback,
        "window_capped": False,
    }


def _paydays_window(
    *,
    as_of: date,
    paydays: list[date],
    payday_count: int,
    window_capped: bool = False,
) -> dict[str, Any]:
    """Build paydays window dict. When capped, end is as_of+45 with partial paydays list."""
    if window_capped:
        end = as_of + timedelta(days=MAX_PAYDAY_HORIZON_DAYS)
        if len(paydays) == 0:
            n_label = "no payday in the next few weeks"
        elif len(paydays) == 1 and payday_count >= 2:
            n_label = "next payday · 2nd is more than a few weeks out"
        else:
            n_label = "next few weeks · waiting on another payday"
        label = f"Until {_label_date(end)} ({n_label})"
    else:
        end = paydays[min(len(paydays), payday_count) - 1] if paydays else as_of
        n_label = "next payday" if payday_count == 1 else "2nd payday"
        label = f"Until {_label_date(end)} ({n_label})"
    return {
        "mode_effective": "paydays",
        "window_start": as_of.isoformat(),
        "window_end": end.isoformat(),
        "calendar_days": None,
        "payday_count": payday_count,
        "paydays": [d.isoformat() for d in paydays[:payday_count]],
        "label": label,
        "window_fallback": None,
        "window_capped": window_capped,
    }


def resolve_window(
    session: Session,
    *,
    as_of: date,
    mode: WindowMode = "auto",
    calendar_days: int = 14,
    payday_count: int = 1,
    profile_id: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Resolve projection window: calendar days or through next N paydays."""
    calendar_days = _clamp_calendar_days(calendar_days)
    payday_count = _clamp_payday_count(payday_count)

    sc, resolved_pid, _note = _resolve_scope_and_profile(
        session, profile_id=profile_id, scope=scope
    )
    mode_raw = (mode or "auto").lower()
    if mode_raw not in ("auto", "calendar", "paydays"):
        mode_raw = "auto"

    income_dates = next_income_dates(
        session,
        as_of=as_of,
        profile_id=resolved_pid,
        scope=sc,
        count=max(payday_count, 2),
        max_days=MAX_PAYDAY_HORIZON_DAYS,
    )

    use_paydays = False
    if mode_raw == "paydays":
        use_paydays = True
    elif mode_raw == "auto":
        use_paydays = bool(income_dates)
    # calendar → False

    if use_paydays:
        if not income_dates:
            return _calendar_window(
                as_of=as_of,
                calendar_days=14,
                window_fallback="no_income",
            )
        n = payday_count
        chosen = income_dates[:n]
        # Not enough paydays within 45d → cap window at horizon (dates already ≤45d)
        if len(chosen) < n:
            return _paydays_window(
                as_of=as_of,
                paydays=chosen,
                payday_count=n,
                window_capped=True,
            )
        return _paydays_window(
            as_of=as_of,
            paydays=chosen,
            payday_count=n,
            window_capped=False,
        )

    return _calendar_window(as_of=as_of, calendar_days=calendar_days)


def _income_certainty_ok(certainty: str, mode: str) -> bool:
    """For listing: show income if weight > 0 under ifpp mode spirit."""
    c = (certainty or "fixed").lower()
    m = (mode or "expected").lower()
    if m == "conservative":
        return c == "fixed"
    if m == "optimistic":
        return True
    # expected / default: fixed + expected + historical_avg all show
    return c in ("fixed", "expected", "historical_avg")


def _settings_window_defaults(settings: AppSettings) -> tuple[WindowMode, int, int, bool]:
    """Read Coming up prefs from AppSettings (with safe fallbacks)."""
    mode_raw = (getattr(settings, "coming_up_window_mode", None) or "auto").lower()
    if mode_raw not in ("auto", "calendar", "paydays"):
        mode_raw = "auto"
    try:
        cal = int(getattr(settings, "coming_up_calendar_days", None) or 14)
    except (TypeError, ValueError):
        cal = 14
    try:
        pd = int(getattr(settings, "coming_up_payday_count", None) or 1)
    except (TypeError, ValueError):
        pd = 1
    show = getattr(settings, "coming_up_show_income", None)
    if show is None:
        show_income = True
    else:
        show_income = bool(show)
    return mode_raw, cal, pd, show_income  # type: ignore[return-value]


def build_coming_up(
    session: Session,
    *,
    as_of: date | None = None,
    mode: WindowMode | None = None,
    calendar_days: int | None = None,
    payday_count: int | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
    ifpp_mode: str | None = None,
    limit: int = 8,
    show_income: bool | None = None,
) -> dict[str, Any]:
    """Build Coming up list: window + cash-side schedule rows.

    When mode / calendar_days / payday_count / show_income are None, values are
    taken from AppSettings (coming_up_* columns). Explicit args always win.
    """
    as_of = as_of or date.today()
    settings = session.get(AppSettings, 1) or AppSettings(id=1)
    def_mode, def_cal, def_pd, def_show = _settings_window_defaults(settings)
    if mode is None:
        mode = def_mode
    if calendar_days is None:
        calendar_days = def_cal
    if payday_count is None:
        payday_count = def_pd
    if show_income is None:
        show_income = def_show
    ifpp_mode = ifpp_mode or getattr(settings, "ifpp_mode", None) or "expected"
    limit = max(1, min(int(limit), 50))

    window = resolve_window(
        session,
        as_of=as_of,
        mode=mode,
        calendar_days=calendar_days,
        payday_count=payday_count,
        profile_id=profile_id,
        scope=scope,
    )
    window_end = date.fromisoformat(window["window_end"])

    sc, resolved_pid, _note = _resolve_scope_and_profile(
        session, profile_id=profile_id, scope=scope
    )
    accounts = _active_accounts(session, resolved_pid, sc)
    credit_ids = {int(a.id) for a in accounts if a.kind == "credit"}
    acct_by_id = {int(a.id): a for a in accounts}

    sched_q = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if sc == "entity" and resolved_pid is not None:
        sched_q = sched_q.filter(ScheduledItem.profile_id == resolved_pid)

    rows: list[dict[str, Any]] = []
    for s in sched_q.all():
        if _is_credit_account_schedule(s, credit_ids):
            continue
        amt = _d(s.amount)
        cert = s.certainty or "fixed"
        kind_raw = (s.kind or "").lower()
        is_owner_draw = kind_raw == "owner_draw"
        # owner_draw is never income for payday / inflow display
        is_income = False if is_owner_draw else is_income_schedule(s)

        if is_income and not show_income:
            continue
        if is_income and not _income_certainty_ok(cert, ifpp_mode):
            continue

        # Mis-tagged income (kind=income, negative amount): treat as positive inflow for display
        expand_amt = amt
        if is_income and expand_amt < ZERO:
            expand_amt = abs(expand_amt)
        # owner_draw is always a cash outflow (normalize sign if mis-stored)
        if is_owner_draw and expand_amt > ZERO:
            expand_amt = -expand_amt

        occ = expand_scheduled(
            item_id=s.id,
            name=s.name or ("Income" if is_income else "Expense"),
            amount=expand_amt,
            next_date=s.next_date,
            cadence=s.cadence or "monthly",
            certainty=cert,
            as_of=as_of,
            horizon_end=window_end,
            end_date=getattr(s, "end_date", None),
            start_date=getattr(s, "start_date", None),
            active=bool(s.active),
        )
        for ev in occ:
            if ev.on_date < as_of or ev.on_date > window_end:
                continue
            ev_amt = _d(ev.amount)
            if ev_amt == ZERO:
                continue
            # owner_draw always out even if amount were positive
            if is_owner_draw:
                direction = "out"
                if ev_amt > ZERO:
                    ev_amt = -ev_amt
            else:
                direction = "in" if ev_amt > ZERO else "out"
            # Skip income occurrences when show_income is False (already gated at schedule)
            if direction == "in" and not show_income:
                continue
            if direction == "in" and not _income_certainty_ok(ev.certainty, ifpp_mode):
                continue
            # Expenses / owner_draw always listed
            acct: Account | None = None
            if s.account_id is not None:
                acct = acct_by_id.get(int(s.account_id)) or session.get(Account, int(s.account_id))
            name = ev.name or s.name or ""
            if is_owner_draw:
                row_kind = "owner_draw"
            elif direction == "in":
                row_kind = "income"
            else:
                row_kind = "expense"
            rows.append(
                {
                    "on_date": ev.on_date.isoformat(),
                    "weekday": ev.on_date.strftime("%a"),
                    "name": name,
                    "amount": str(_q(ev_amt)),
                    "direction": direction,
                    "account_id": int(s.account_id) if s.account_id is not None else None,
                    "account_name": acct.nickname if acct is not None else None,
                    "scheduled_id": int(s.id),
                    "kind": row_kind,
                    "is_card_payment": name.startswith("Card payment"),
                    "_sort_abs": abs(ev_amt),
                }
            )

    # Sort: date asc, then larger absolute amount first
    rows.sort(key=lambda r: (r["on_date"], -r["_sort_abs"]))
    for r in rows:
        r.pop("_sort_abs", None)

    # Totals over the full window; items may be truncated for the list
    full_count = len(rows)
    outflow = sum((_d(r["amount"]) for r in rows if r["direction"] == "out"), ZERO)
    inflow = sum((_d(r["amount"]) for r in rows if r["direction"] == "in"), ZERO)
    outflow_total = _q(abs(outflow))
    inflow_total = _q(inflow)
    limited = rows[:limit]
    truncated = full_count > limit

    return {
        "window": window,
        "items": limited,
        "outflow_total": str(outflow_total),
        "inflow_total": str(inflow_total),
        "count": full_count,
        "shown_count": len(limited),
        "truncated": truncated,
        "empty_hint": EMPTY_HINT if full_count == 0 else None,
    }
