"""Autopay desk: min / statement / promo_sink policies per credit account."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, ScheduledItem
from financial_os.engine.ifpp import next_due_date
from financial_os.services.promo_sink import create_promo_sink_bill

ZERO = Decimal("0")
POLICIES = frozenset({"none", "min", "statement", "promo_sink"})


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def list_autopay(
    session: Session,
    *,
    profile_id: int | None = None,
) -> dict[str, Any]:
    q = session.query(Account).filter(
        Account.kind == "credit",
        Account.archived_at.is_(None),
    )
    if profile_id is not None:
        q = q.filter(Account.profile_id == profile_id)
    items = []
    for a in q.order_by(Account.id).all():
        policy = (getattr(a, "autopay_policy", None) or "none").lower()
        bal = _d(a.current_balance)
        mn = _d(a.min_payment) if a.min_payment is not None else (bal * Decimal("0.02")).quantize(Decimal("0.01"))
        items.append(
            {
                "account_id": a.id,
                "name": a.nickname,
                "balance": str(bal),
                "min_payment": str(mn),
                "payment_due_day": a.payment_due_day,
                "policy": policy if policy in POLICIES else "none",
                "suggested_amount": str(
                    _suggested_amount(a, policy if policy in POLICIES else "none")
                ),
            }
        )
    return {
        "count": len(items),
        "items": items,
        "policies": sorted(POLICIES),
        "hint": "min = minimum due; statement = pay in full; promo_sink = monthly 0% balloon fund.",
    }


def _suggested_amount(a: Account, policy: str) -> Decimal:
    bal = max(ZERO, _d(a.current_balance))
    if policy == "min":
        if a.min_payment is not None:
            return _d(a.min_payment)
        return (bal * Decimal("0.02")).quantize(Decimal("0.01")) or Decimal("25")
    if policy == "statement":
        return bal
    if policy == "promo_sink":
        # approximate monthly if promo bal set
        promo = _d(a.promo_balance) if a.promo_balance is not None else bal
        if a.promo_end_date:
            days = max(1, (a.promo_end_date - date.today()).days)
            months = max(1, days // 30)
            return (promo / Decimal(months)).quantize(Decimal("0.01"))
        return (promo / Decimal("12")).quantize(Decimal("0.01"))
    return ZERO


def set_autopay(
    session: Session,
    *,
    account_id: int,
    policy: str,
    apply_schedule: bool = True,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    policy = (policy or "none").lower()
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {sorted(POLICIES)}")
    a = session.get(Account, account_id)
    if not a or a.kind != "credit":
        raise ValueError("Credit account not found")
    a.autopay_policy = policy
    session.flush()

    schedule_result = None
    if apply_schedule and policy != "none":
        schedule_result = _sync_schedule(session, a, policy, as_of=as_of)
    elif apply_schedule and policy == "none":
        _end_autopay_schedules(session, a.id)

    return {
        "ok": True,
        "account_id": a.id,
        "name": a.nickname,
        "policy": policy,
        "suggested_amount": str(_suggested_amount(a, policy)),
        "schedule": schedule_result,
    }


def _end_autopay_schedules(session: Session, account_id: int) -> None:
    rows = (
        session.query(ScheduledItem)
        .filter(
            ScheduledItem.account_id == account_id,
            ScheduledItem.active.is_(True),
            ScheduledItem.name.like("Autopay ·%"),
        )
        .all()
    )
    for r in rows:
        r.active = False
        r.ended_reason = "autopay policy none"
    session.flush()


def _sync_schedule(
    session: Session,
    a: Account,
    policy: str,
    *,
    as_of: date,
) -> dict[str, Any]:
    if policy == "promo_sink":
        try:
            return create_promo_sink_bill(session, account_id=a.id, as_of=as_of)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    amt = _suggested_amount(a, policy)
    if amt <= ZERO:
        return {"ok": False, "error": "amount is zero"}

    due = next_due_date(as_of, a.payment_due_day) or as_of
    name = f"Autopay · {policy} · {a.nickname}"
    existing = (
        session.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.account_id == a.id,
            ScheduledItem.name == name,
        )
        .first()
    )
    if existing:
        existing.amount = -abs(amt)
        existing.next_date = due
        existing.notes = f"Autopay policy={policy}"
        session.flush()
        return {
            "ok": True,
            "created": False,
            "updated": True,
            "scheduled_id": existing.id,
            "name": name,
            "amount": str(existing.amount),
            "next_date": due.isoformat(),
        }

    row = ScheduledItem(
        profile_id=a.profile_id,
        name=name,
        amount=-abs(amt),
        next_date=due,
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        account_id=a.id,
        notes=f"Autopay policy={policy}",
        active=True,
    )
    session.add(row)
    session.flush()
    return {
        "ok": True,
        "created": True,
        "scheduled_id": row.id,
        "name": name,
        "amount": str(row.amount),
        "next_date": due.isoformat(),
    }
