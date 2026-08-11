"""Autopay desk: min / statement / fixed / promo_sink policies per credit account.

Cash-funded schedules (`Card payment · {nickname}`) live on the funding checking
account and are rewritten by recompute after every credit balance change.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, ScheduledItem
from financial_os.engine.ifpp import next_due_date
from financial_os.services.promo_sink import create_promo_sink_bill
from financial_os.services.statement_cycle import project_card_payment

ZERO = Decimal("0")
POLICIES = frozenset({"none", "min", "statement", "promo_sink", "fixed"})
# setup wizard payment_option → autopay_policy
PAYMENT_OPTION_TO_POLICY = {
    "minimum": "min",
    "statement": "statement",
    "fixed": "fixed",
    "interest_saving": "promo_sink",
}
CARD_PAYMENT_NOTES_AUTO = "auto=statement_cycle"


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _card_payment_name(nickname: str) -> str:
    return f"Card payment · {nickname}"


def _notes_for_card(card_id: int, policy: str) -> str:
    return f"card_account_id={card_id}; policy={policy}; {CARD_PAYMENT_NOTES_AUTO}"


def _notes_marker(card_id: int) -> str:
    return f"card_account_id={card_id};"


def ensure_autopay_policy_from_payment_option(account: Account) -> str:
    """Return effective policy; backfill only when autopay_policy is null/empty.

    Explicit ``autopay_policy="none"`` is sticky — do not overwrite from
    ``payment_option``. Only null/blank policies are filled from the wizard map.
    """
    raw = getattr(account, "autopay_policy", None)
    if raw is not None and str(raw).strip() != "":
        policy = str(raw).lower().strip()
        return policy if policy in POLICIES else "none"
    opt = (getattr(account, "payment_option", None) or "").lower().strip()
    mapped = PAYMENT_OPTION_TO_POLICY.get(opt)
    if mapped:
        account.autopay_policy = mapped
        return mapped
    return "none"


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
        if policy not in POLICIES:
            policy = "none"
        bal = _d(a.current_balance)
        mn = (
            _d(a.min_payment)
            if a.min_payment is not None
            else (bal * Decimal("0.02")).quantize(Decimal("0.01"))
        )
        items.append(
            {
                "account_id": a.id,
                "name": a.nickname,
                "balance": str(bal),
                "min_payment": str(mn),
                "payment_due_day": a.payment_due_day,
                "policy": policy,
                "suggested_amount": str(_suggested_amount(a, policy)),
                "funding_account_id": a.payment_funding_account_id,
                "next_payment_cached": (
                    str(a.next_payment_amount_cached)
                    if a.next_payment_amount_cached is not None
                    else None
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
    if policy == "fixed":
        if a.payment_fixed_amount is not None:
            return _d(a.payment_fixed_amount)
        return ZERO
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
    if apply_schedule:
        if policy == "none":
            _end_card_payment_schedules(session, a.id)
            a.statement_balance_cached = None
            a.next_payment_amount_cached = None
            a.next_payment_date_cached = None
            session.flush()
        elif policy == "promo_sink":
            # Prefer cash recompute; fall back to legacy promo sink bill if amount is 0
            recomputed = recompute_card_payment_schedule(session, a.id, as_of=as_of)
            schedule_result = recomputed.get("schedule")
            if not schedule_result or not schedule_result.get("ok"):
                try:
                    schedule_result = create_promo_sink_bill(
                        session, account_id=a.id, as_of=as_of
                    )
                except ValueError as e:
                    schedule_result = {"ok": False, "error": str(e)}
        else:
            recomputed = recompute_card_payment_schedule(session, a.id, as_of=as_of)
            schedule_result = recomputed.get("schedule")

    return {
        "ok": True,
        "account_id": a.id,
        "name": a.nickname,
        "policy": policy,
        "suggested_amount": str(_suggested_amount(a, policy)),
        "schedule": schedule_result,
    }


def after_account_balance_changed(
    session: Session,
    account_id: int,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Hook after books balance mutation — recompute credit payment schedule/caches."""
    acct = session.get(Account, account_id)
    if not acct or (acct.kind or "") != "credit":
        return {"ok": False, "skipped": True, "reason": "not_credit", "account_id": account_id}
    return recompute_card_payment_schedule(session, account_id, as_of=as_of)


def recompute_all_card_payments(
    session: Session,
    profile_id: int | None = None,
    *,
    as_of: date | None = None,
) -> int:
    """Recompute all non-archived credit cards; returns count processed."""
    q = session.query(Account).filter(
        Account.kind == "credit",
        Account.archived_at.is_(None),
    )
    if profile_id is not None:
        q = q.filter(Account.profile_id == profile_id)
    n = 0
    for a in q.order_by(Account.id).all():
        recompute_card_payment_schedule(session, a.id, as_of=as_of)
        n += 1
    return n


def recompute_card_payment_schedule(
    session: Session,
    account_id: int,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Project statement/next payment, write Account caches, upsert cash schedule.

    Schedules sit on the funding cash account (not the card). Amount may exceed
    available cash — schedules are future; never-neg runs at actual payment time.
    """
    as_of = as_of or date.today()
    a = session.get(Account, account_id)
    if not a:
        return {"ok": False, "error": "Account not found", "account_id": account_id}
    if (a.kind or "") != "credit":
        return {
            "ok": False,
            "skipped": True,
            "reason": "not_credit",
            "account_id": account_id,
        }

    ensure_autopay_policy_from_payment_option(a)
    session.flush()

    proj = project_card_payment(session, account_id, as_of=as_of)
    policy = (proj.get("policy") or "none").lower().strip()
    next_payment = _d(proj.get("next_payment"))
    next_due = proj.get("next_due")
    statement_balance = _d(proj.get("statement_balance"))

    a.statement_balance_cached = statement_balance
    a.next_payment_amount_cached = next_payment
    a.next_payment_date_cached = next_due

    schedule_result: dict[str, Any] | None
    if policy == "none" or next_payment <= ZERO:
        _end_card_payment_schedules(session, a.id)
        schedule_result = {
            "ok": False,
            "ended": True,
            "reason": "policy_none_or_zero_payment",
        }
    else:
        funding_id = proj.get("funding_account_id") or a.payment_funding_account_id
        if not funding_id:
            # Policy active but no funding: end stale cash schedules so IFPP
            # does not keep counting a ghost outflow for this card.
            _end_card_payment_schedules(session, a.id)
            schedule_result = {
                "ok": False,
                "ended": True,
                "error": "no funding account (payment_funding_account_id)",
            }
        else:
            schedule_result = _sync_schedule(
                session,
                a,
                policy,
                as_of=as_of,
                amount=next_payment,
                next_date=next_due,
                funding_account_id=int(funding_id),
            )
            # Drop legacy Autopay · rows on the card (cash path owns the payment)
            _end_autopay_schedules(session, a.id)

    session.flush()
    return {
        "ok": True,
        "account_id": a.id,
        "statement_balance": statement_balance,
        "next_payment": next_payment,
        "next_due": next_due,
        "last_close": proj.get("last_close"),
        "next_close": proj.get("next_close"),
        "funding_account_id": proj.get("funding_account_id") or a.payment_funding_account_id,
        "policy": policy,
        "schedule": schedule_result,
        "scheduled_id": (schedule_result or {}).get("scheduled_id"),
    }


def _end_autopay_schedules(session: Session, account_id: int) -> None:
    """End legacy Autopay · schedules attached to the credit account."""
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


def _end_card_payment_schedules(session: Session, card_account_id: int) -> None:
    """End cash-funded Card payment schedules tagged for this card + legacy Autopay."""
    marker = _notes_marker(card_account_id)
    rows = (
        session.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.notes.like(f"%{marker}%"),
        )
        .all()
    )
    for r in rows:
        r.active = False
        r.ended_reason = "autopay policy none / recompute"
    _end_autopay_schedules(session, card_account_id)
    session.flush()


def _find_card_payment_schedule(
    session: Session,
    card: Account,
    *,
    funding_account_id: int | None,
) -> ScheduledItem | None:
    marker = _notes_marker(card.id)
    by_notes = (
        session.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.notes.like(f"%{marker}%"),
        )
        .first()
    )
    if by_notes:
        return by_notes
    name = _card_payment_name(card.nickname)
    if funding_account_id:
        return (
            session.query(ScheduledItem)
            .filter(
                ScheduledItem.active.is_(True),
                ScheduledItem.account_id == funding_account_id,
                ScheduledItem.name == name,
            )
            .first()
        )
    return None


def _sync_schedule(
    session: Session,
    a: Account,
    policy: str,
    *,
    as_of: date,
    amount: Decimal | None = None,
    next_date: date | None = None,
    funding_account_id: int | None = None,
) -> dict[str, Any]:
    """Upsert cash-funded Card payment schedule (or legacy promo_sink on card).

    When amount/funding are provided (recompute path), uses statement projection.
    Without them, falls back to _suggested_amount for set_autopay legacy callers.
    """
    if policy == "promo_sink" and amount is None:
        try:
            return create_promo_sink_bill(session, account_id=a.id, as_of=as_of)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    if amount is not None:
        amt = abs(_d(amount))
    else:
        amt = abs(_suggested_amount(a, policy))
    if amt <= ZERO:
        return {"ok": False, "error": "amount is zero"}

    funding_id = funding_account_id or a.payment_funding_account_id
    if not funding_id:
        # Legacy fallback: schedule on the card (IFPP skips credit schedules)
        funding_id = a.id
        name = f"Autopay · {policy} · {a.nickname}"
        notes = f"Autopay policy={policy}"
        due = next_date or next_due_date(as_of, a.payment_due_day) or as_of
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
            existing.amount = -amt
            existing.next_date = due
            existing.notes = notes
            session.flush()
            return {
                "ok": True,
                "created": False,
                "updated": True,
                "scheduled_id": existing.id,
                "name": name,
                "amount": str(existing.amount),
                "next_date": due.isoformat() if hasattr(due, "isoformat") else str(due),
                "funding_account_id": a.id,
                "legacy_card_schedule": True,
            }
        row = ScheduledItem(
            profile_id=a.profile_id,
            name=name,
            amount=-amt,
            next_date=due,
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=a.id,
            notes=notes,
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
            "next_date": due.isoformat() if hasattr(due, "isoformat") else str(due),
            "funding_account_id": a.id,
            "legacy_card_schedule": True,
        }

    due = next_date or next_due_date(as_of, a.payment_due_day) or as_of
    name = _card_payment_name(a.nickname)
    notes = _notes_for_card(a.id, policy)
    existing = _find_card_payment_schedule(
        session, a, funding_account_id=int(funding_id)
    )
    if existing:
        existing.amount = -amt
        existing.next_date = due
        existing.account_id = int(funding_id)
        existing.name = name
        existing.notes = notes
        existing.kind = "expense"
        existing.certainty = "fixed"
        existing.cadence = existing.cadence or "monthly"
        session.flush()
        return {
            "ok": True,
            "created": False,
            "updated": True,
            "scheduled_id": existing.id,
            "name": name,
            "amount": str(existing.amount),
            "next_date": due.isoformat() if hasattr(due, "isoformat") else str(due),
            "funding_account_id": int(funding_id),
        }

    row = ScheduledItem(
        profile_id=a.profile_id,
        name=name,
        amount=-amt,
        next_date=due,
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        account_id=int(funding_id),
        notes=notes,
        active=True,
    )
    session.add(row)
    session.flush()
    return {
        "ok": True,
        "created": True,
        "updated": False,
        "scheduled_id": row.id,
        "name": name,
        "amount": str(row.amount),
        "next_date": due.isoformat() if hasattr(due, "isoformat") else str(due),
        "funding_account_id": int(funding_id),
    }
