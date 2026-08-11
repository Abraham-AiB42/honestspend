"""Day-by-day cash runway strip (30–45d) for Safe to spend honesty.

Builds a calendar of running cash after buffer/tax vault and cash-side schedules
(card-account schedules skipped — same rule as IFPP). First red day is the first
date ending balance would go below zero.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, AppSettings, Profile, ScheduledItem
from financial_os.engine.ifpp import CashAccountView, ScheduledView
from financial_os.engine.schedule_expand import expand_scheduled
from financial_os.services.ifpp_service import (
    _active_accounts,
    _is_credit_account_schedule,
    _resolve_scope_and_profile,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return _d(v).quantize(CENT)


def runway_starting_cash(
    session: Session,
    *,
    profile_id: int | None = None,
    scope: str | None = None,
) -> tuple[Decimal, str | None, str, dict[str, Any]]:
    """Cash after buffer + tax vault — same start as build_cash_runway.

    Returns (start, resolved_pid, scope, details) where details has raw_cash,
    buffer_reserved, tax_vault_reserved.
    """
    settings = session.get(AppSettings, 1) or AppSettings(id=1)
    sc, resolved_pid, _scope_note = _resolve_scope_and_profile(
        session, profile_id=profile_id, scope=scope
    )
    accounts = _active_accounts(session, resolved_pid, sc)

    cash_views = [
        CashAccountView(
            id=a.id,
            name=a.nickname,
            balance=_d(a.current_balance),
            is_cash_for_ifpp=bool(a.is_cash_for_ifpp),
            kind=a.kind or "checking",
            safety_buffer=_d(getattr(a, "safety_buffer", None) or 0),
        )
        for a in accounts
        if a.kind in ("checking", "savings", "cash") or a.is_cash_for_ifpp
    ]

    # Same pool rules as compute_cash_spendable (checking preferred)
    scope_neg = (getattr(settings, "never_negative_scope", None) or "checking").lower()
    if scope_neg == "checking":
        pool = [a for a in cash_views if a.is_cash_for_ifpp and a.kind == "checking"]
        if not pool:
            pool = [a for a in cash_views if a.is_cash_for_ifpp]
    else:
        pool = [a for a in cash_views if a.is_cash_for_ifpp]

    raw_cash = sum((_d(a.balance) for a in pool), ZERO)
    per_acct = sum(
        (
            min(max(ZERO, _d(getattr(a, "safety_buffer", ZERO) or ZERO)), max(ZERO, _d(a.balance)))
            for a in pool
        ),
        ZERO,
    )
    total_floor = max(ZERO, _d(settings.safety_buffer if settings.safety_buffer is not None else 1000))
    effective_buffer = max(total_floor, per_acct)

    tax_vault = ZERO
    if getattr(settings, "tax_vault_enabled", True):
        tax_vault = _d(getattr(settings, "tax_vault_balance", None) or 0)
        if sc == "entity" and resolved_pid is not None:
            default = session.query(Profile).filter(Profile.is_default.is_(True)).first()
            if default and resolved_pid != default.id:
                tax_vault = ZERO

    start = _q(raw_cash - effective_buffer - tax_vault)
    details = {
        "raw_cash": _q(raw_cash),
        "buffer_reserved": _q(effective_buffer),
        "tax_vault_reserved": _q(tax_vault),
        "accounts": accounts,
        "scope_note": _scope_note,
    }
    return start, resolved_pid, sc, details


def _label_window_end(d: date) -> str:
    """Plain English short date: 'Fri Mar 6'."""
    return f"{d.strftime('%a')} {d.strftime('%b')} {d.day}"


def build_safe_until_window(
    session: Session,
    *,
    as_of: date | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
    coming_up: dict[str, Any] | None = None,
    cap_at: Decimal | None = None,
    starting_cash: Decimal | None = None,
) -> dict[str, Any]:
    """Soft cash left after window outflows (does not replace safe_to_spend).

    Default start is runway cash (after buffer/tax vault, **no** schedule
    effects). Home should pass ``starting_cash`` = max(0, runway_start −
    budget_reserve) so budgets match STS without using IFPP min_running as the
    base (that would double-count Coming up bills already in cash_spendable).

    Formula: soft = max(0, start − window outflows), then if ``cap_at`` is set
    (typically Home Safe to spend / STS), amount never exceeds that primary
    number — one cash spine for Simple Home.
    """
    as_of = as_of or date.today()
    if coming_up is None:
        from financial_os.services.coming_up import build_coming_up

        coming_up = build_coming_up(
            session,
            as_of=as_of,
            profile_id=profile_id,
            scope=scope,
        )

    window = coming_up.get("window") or {}
    window_end_s = window.get("window_end") or as_of.isoformat()
    try:
        window_end = date.fromisoformat(str(window_end_s)[:10])
    except ValueError:
        window_end = as_of

    outflows = _q(_d(coming_up.get("outflow_total")))
    if starting_cash is not None:
        start = _q(_d(starting_cash))
    else:
        start, _pid, _sc, _det = runway_starting_cash(
            session, profile_id=profile_id, scope=scope
        )
    raw_amount = _q(max(ZERO, start - outflows))
    capped = False
    amount = raw_amount
    if cap_at is not None:
        cap_q = _q(max(ZERO, _d(cap_at)))
        if raw_amount > cap_q:
            amount = cap_q
            capped = True

    end_label = _label_window_end(window_end)
    mode_eff = (window.get("mode_effective") or "calendar").lower()
    if mode_eff == "paydays":
        if window.get("window_capped"):
            # Plain English — never "horizon" in user-facing labels
            reason = "next few weeks · waiting on another payday"
            if window.get("paydays") and len(window.get("paydays") or []) == 1:
                reason = "next payday · looking only a few weeks out"
            label = f"Safe until {end_label} ({reason})"
        else:
            pd = int(window.get("payday_count") or 1)
            reason = "next payday" if pd <= 1 else "2nd payday"
            label = f"Safe until {end_label} ({reason})"
    else:
        cal = window.get("calendar_days")
        if cal is None:
            try:
                cal = max(0, (window_end - as_of).days)
            except Exception:
                cal = 14
        if window.get("window_fallback") == "no_income":
            label = f"Safe until {end_label} (next {cal} days · no paycheck scheduled)"
        else:
            label = f"Safe until {end_label} (next {cal} days)"
    if capped:
        label = f"{label} · within Safe to spend"

    return {
        "amount": str(amount),
        "raw_amount": str(raw_amount),
        "capped_to_safe_to_spend": capped,
        "window_end": window_end.isoformat(),
        "label": label,
        "starting_cash": str(start),
        "outflows": str(outflows),
    }


def build_cash_runway(
    session: Session,
    *,
    as_of: date | None = None,
    days: int | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Return day strip: starting cash, per-day events, ending balance, first red day."""
    as_of = as_of or date.today()
    settings = session.get(AppSettings, 1) or AppSettings(id=1)
    mode = mode or settings.ifpp_mode or "expected"
    horizon = int(days if days is not None else (settings.horizon_days or 45))
    horizon = max(7, min(horizon, 90))
    horizon_end = as_of + timedelta(days=horizon)

    start, resolved_pid, sc, det = runway_starting_cash(
        session, profile_id=profile_id, scope=scope
    )
    scope_note = det.get("scope_note")
    accounts = det["accounts"]
    raw_cash = det["raw_cash"]
    effective_buffer = det["buffer_reserved"]
    tax_vault = det["tax_vault_reserved"]

    credit_ids = {int(a.id) for a in accounts if a.kind == "credit"}
    sched_q = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if sc == "entity" and resolved_pid is not None:
        sched_q = sched_q.filter(ScheduledItem.profile_id == resolved_pid)

    cliff_on = bool(getattr(settings, "income_cliff_enabled", False))
    cliff_factor = _d(getattr(settings, "income_cliff_factor", None) or "1")
    if cliff_factor < 0:
        cliff_factor = ZERO
    if cliff_factor > 1:
        cliff_factor = Decimal("1")

    scheduled: list[ScheduledView] = []
    for s in sched_q.all():
        if _is_credit_account_schedule(s, credit_ids):
            continue
        amt = _d(s.amount)
        cert = s.certainty or "fixed"
        if cliff_on and amt > 0 and cert in ("expected", "historical_avg"):
            amt = (amt * cliff_factor).quantize(CENT)
        scheduled.extend(
            expand_scheduled(
                item_id=s.id,
                name=s.name,
                amount=amt,
                next_date=s.next_date,
                cadence=s.cadence or "monthly",
                certainty=cert,
                as_of=as_of,
                horizon_end=horizon_end,
                end_date=getattr(s, "end_date", None),
                start_date=getattr(s, "start_date", None),
                active=bool(s.active),
            )
        )

    # Group events by day
    by_day: dict[date, list[ScheduledView]] = {}
    for ev in scheduled:
        by_day.setdefault(ev.on_date, []).append(ev)

    def _weight(cert: str) -> Decimal:
        c = (cert or "fixed").lower()
        if mode == "conservative":
            if c == "fixed":
                return Decimal("1")
            if c == "expected":
                return Decimal("0.5")
            return ZERO  # historical ignored as income in conservative
        if mode == "optimistic":
            return Decimal("1")
        # expected / default
        if c == "historical_avg":
            return Decimal("0.75")
        return Decimal("1")

    days_out: list[dict[str, Any]] = []
    running = start
    min_bal = start
    first_red: date | None = as_of if start < ZERO else None
    red_now = start < ZERO

    for i in range(horizon + 1):
        d = as_of + timedelta(days=i)
        events = by_day.get(d, [])
        day_in = ZERO
        day_out = ZERO
        event_rows = []
        for ev in events:
            amt = _d(ev.amount)
            if amt > 0:
                w = _weight(ev.certainty)
                if w == ZERO:
                    continue
                delta = _q(amt * w)
                day_in += delta
                running += delta
            else:
                delta = amt  # full outflow
                day_out += abs(delta)
                running += delta
            event_rows.append(
                {
                    "name": ev.name,
                    "amount": str(_q(delta if amt > 0 else amt)),
                    "certainty": ev.certainty,
                }
            )
        ending = _q(running)
        if ending < min_bal:
            min_bal = ending
        is_red = ending < ZERO
        if is_red and first_red is None:
            first_red = d
        days_out.append(
            {
                "date": d.isoformat(),
                "inflows": str(_q(day_in)),
                "outflows": str(_q(day_out)),
                "ending_balance": str(ending),
                "is_red": is_red,
                "event_count": len(event_rows),
                "events": event_rows,
            }
        )

    # Compact strip for UI: only days with activity or red or first/last
    highlight = [
        row
        for row in days_out
        if row["event_count"] > 0 or row["is_red"] or row["date"] in (as_of.isoformat(), horizon_end.isoformat())
    ]

    return {
        "as_of": as_of.isoformat(),
        "horizon_days": horizon,
        "horizon_end": horizon_end.isoformat(),
        "ifpp_scope": sc,
        "profile_id": resolved_pid,
        "scope_note": scope_note,
        "mode": mode,
        "starting_cash": str(start),
        "raw_cash": str(_q(raw_cash)),
        "buffer_reserved": str(_q(effective_buffer)),
        "tax_vault_reserved": str(_q(tax_vault)),
        "min_balance": str(_q(min_bal)),
        "first_red_day": first_red.isoformat() if first_red else None,
        "is_red_now": red_now,
        "days": days_out,
        "highlight_days": highlight,
        "day_count": len(days_out),
        "hint": "Day strip of cash after buffer and tax vault. Card bills on credit accounts are not double-counted — only cash-side schedules (including Card payment · …) appear here.",
    }
