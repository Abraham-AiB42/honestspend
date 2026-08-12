"""Freeze statement cycles with actual balances (bank statement / import / user)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, StatementCycle
from honestspend.engine.ifpp import next_due_date
from honestspend.services.statement_cycle import (
    previous_close_date,
    project_card_payment,
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


def freeze_statement_cycle(
    session: Session,
    account_id: int,
    *,
    actual_balance: Decimal,
    cycle_end: date | None = None,
    due_date: date | None = None,
    cycle_start: date | None = None,
    payment_amount: Decimal | None = None,
    source: str = "user",
    as_of: date | None = None,
) -> dict[str, Any]:
    """Record or update a closed statement cycle with the bank's actual balance.

    If cycle_end omitted, uses last close on or before as_of.
    """
    as_of = as_of or date.today()
    acct = session.get(Account, account_id)
    if not acct:
        raise ValueError(f"Account {account_id} not found")
    if (acct.kind or "") != "credit":
        raise ValueError("Statement freeze applies to credit accounts only")

    close_day = int(acct.statement_close_day) if acct.statement_close_day else 1
    close_day = max(1, min(31, close_day))

    end = cycle_end or previous_close_date(as_of, close_day)
    # Cycle start = day after previous previous close, or one month back
    if cycle_start is None:
        # start = day after the close before `end`
        prior = previous_close_date(end - __import__("datetime").timedelta(days=1), close_day)
        if prior >= end:
            # same day edge: go back a month via next_close reverse
            from honestspend.services.statement_cycle import _close_in_month

            y, m = end.year, end.month - 1
            if m < 1:
                m = 12
                y -= 1
            prior = _close_in_month(y, m, close_day)
        start = prior
        # Inclusive window often (start, end]; store start as day after prior close
        from datetime import timedelta

        start = prior + timedelta(days=1) if prior < end else prior
    else:
        start = cycle_start

    due = due_date
    if due is None:
        due = next_due_date(end, acct.payment_due_day) or end

    proj = None
    try:
        proj = project_card_payment(session, account_id, as_of=end)
    except Exception:
        proj = None
    projected = _q(_d(proj["statement_balance"])) if proj else None
    pay_amt = (
        _q(_d(payment_amount))
        if payment_amount is not None
        else (_q(_d(proj["next_payment"])) if proj else _q(_d(actual_balance)))
    )

    # Upsert by account + cycle_end
    row = (
        session.query(StatementCycle)
        .filter(
            StatementCycle.account_id == account_id,
            StatementCycle.cycle_end == end,
        )
        .first()
    )
    if not row:
        row = StatementCycle(
            account_id=account_id,
            cycle_start=start,
            cycle_end=end,
            due_date=due,
            status="closed",
            source=(source or "user").strip() or "user",
        )
        session.add(row)
    row.cycle_start = start
    row.cycle_end = end
    row.due_date = due
    row.actual_balance = _q(_d(actual_balance))
    row.projected_balance = projected
    row.payment_amount = pay_amt
    row.payment_funding_account_id = acct.payment_funding_account_id
    row.status = "closed"
    row.source = (source or "user").strip() or "user"
    session.flush()

    variance = None
    if projected is not None:
        variance = _q(_d(actual_balance) - projected)

    return {
        "id": row.id,
        "account_id": account_id,
        "cycle_start": row.cycle_start.isoformat(),
        "cycle_end": row.cycle_end.isoformat(),
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "actual_balance": str(row.actual_balance),
        "projected_balance": str(projected) if projected is not None else None,
        "variance": str(variance) if variance is not None else None,
        "payment_amount": str(row.payment_amount) if row.payment_amount is not None else None,
        "status": row.status,
        "source": row.source,
    }


def list_statement_cycles(
    session: Session,
    account_id: int,
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    rows = (
        session.query(StatementCycle)
        .filter(StatementCycle.account_id == account_id)
        .order_by(StatementCycle.cycle_end.desc())
        .limit(max(1, min(limit, 120)))
        .all()
    )
    out = []
    for r in rows:
        variance = None
        if r.actual_balance is not None and r.projected_balance is not None:
            variance = _q(_d(r.actual_balance) - _d(r.projected_balance))
        out.append(
            {
                "id": r.id,
                "account_id": r.account_id,
                "cycle_start": r.cycle_start.isoformat() if r.cycle_start else None,
                "cycle_end": r.cycle_end.isoformat() if r.cycle_end else None,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "actual_balance": str(r.actual_balance) if r.actual_balance is not None else None,
                "projected_balance": str(r.projected_balance)
                if r.projected_balance is not None
                else None,
                "variance": str(variance) if variance is not None else None,
                "payment_amount": str(r.payment_amount) if r.payment_amount is not None else None,
                "status": r.status,
                "source": r.source,
            }
        )
    return out
