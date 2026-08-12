"""Account reconcile: books balance vs institution balance."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.orm import Session

from honestspend.db import Account, Profile

ZERO = Decimal("0")
Trust = Literal["books", "institution"]


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def reconcile_report(
    session: Session,
    *,
    profile_id: int | None = None,
    drift_threshold: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    q = session.query(Account).filter(Account.archived_at.is_(None))
    if profile_id is not None:
        q = q.filter(Account.profile_id == profile_id)
    profiles = {p.id: p for p in session.query(Profile).all()}
    rows: list[dict[str, Any]] = []
    drifted = 0
    for a in q.order_by(Account.profile_id, Account.id).all():
        books = _d(a.current_balance)
        inst = a.institution_balance
        inst_d = _d(inst) if inst is not None else None
        drift = None
        status = "unknown"
        if inst_d is not None:
            drift = (books - inst_d).quantize(Decimal("0.01"))
            if abs(drift) < drift_threshold:
                status = "matched"
            else:
                status = "drift"
                drifted += 1
        else:
            status = "no_institution"
        prof = profiles.get(a.profile_id)
        rows.append(
            {
                "account_id": a.id,
                "nickname": a.nickname,
                "kind": a.kind,
                "profile_id": a.profile_id,
                "profile_name": prof.display_name if prof else None,
                "books_balance": str(books),
                "institution_balance": str(inst_d) if inst_d is not None else None,
                "drift": str(drift) if drift is not None else None,
                "status": status,
                "last_reconciled_at": a.last_reconciled_at.isoformat() if a.last_reconciled_at else None,
                "is_cash_for_ifpp": bool(a.is_cash_for_ifpp),
            }
        )
    return {
        "count": len(rows),
        "drifted": drifted,
        "accounts": rows,
        "principle": "Trust bank when live; books for day-to-day. Resolve drift so Spendable is real.",
    }


def set_institution_balance(
    session: Session,
    account_id: int,
    balance: Decimal,
    *,
    mark_reconciled: bool = False,
) -> dict[str, Any]:
    a = session.get(Account, account_id)
    if not a:
        raise ValueError("Account not found")
    a.institution_balance = balance
    if mark_reconciled:
        a.last_reconciled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.flush()
    return {
        "ok": True,
        "account_id": a.id,
        "institution_balance": str(a.institution_balance),
        "books_balance": str(a.current_balance),
        "last_reconciled_at": a.last_reconciled_at.isoformat() if a.last_reconciled_at else None,
    }


def trust_balance(
    session: Session,
    account_id: int,
    *,
    trust: Trust,
) -> dict[str, Any]:
    """
    trust=institution: set books (current_balance) from institution_balance.
    trust=books: set institution_balance from books and mark reconciled.

    Recalculates credit available_credit. Logs a lightweight audit event.
    """
    a = session.get(Account, account_id)
    if not a:
        raise ValueError("Account not found")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    before_books = _d(a.current_balance)
    before_inst = _d(a.institution_balance) if a.institution_balance is not None else None
    books_changed = False
    if trust == "institution":
        if a.institution_balance is None:
            raise ValueError("No institution balance to trust")
        a.current_balance = _d(a.institution_balance)
        a.last_reconciled_at = now
        books_changed = True
    elif trust == "books":
        a.institution_balance = _d(a.current_balance)
        a.last_reconciled_at = now
    else:
        raise ValueError("trust must be books or institution")
    if a.kind == "credit" and a.credit_limit is not None:
        bal = _d(a.current_balance)
        a.available_credit = _d(a.credit_limit) - bal
    try:
        from honestspend.services.audit import log_event

        log_event(
            session,
            username="local",
            role="owner",
            action=f"trust_{trust}",
            path=f"/api/reconcile/{account_id}/trust",
            detail=(
                f"{a.nickname}: books {before_books}→{_d(a.current_balance)}; "
                f"inst {before_inst}→{_d(a.institution_balance) if a.institution_balance is not None else None}"
            ),
        )
    except Exception:
        pass
    session.flush()
    # Credit books drive statement projection + cash Card payment schedules
    if books_changed and (a.kind or "") == "credit":
        from honestspend.services.autopay import after_account_balance_changed

        after_account_balance_changed(session, int(a.id))
    return {
        "ok": True,
        "account_id": a.id,
        "trusted": trust,
        "books_balance": str(a.current_balance),
        "institution_balance": str(a.institution_balance) if a.institution_balance is not None else None,
        "available_credit": str(a.available_credit) if a.available_credit is not None else None,
        "last_reconciled_at": a.last_reconciled_at.isoformat() if a.last_reconciled_at else None,
        "books_before": str(before_books),
    }
