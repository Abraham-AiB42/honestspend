"""Mark a scheduled expense occurrence as paid and advance the schedule."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, ScheduledItem, Transaction
from financial_os.engine.schedule_expand import next_occurrence
from financial_os.services.account_balance import apply_amount_to_account

ZERO = Decimal("0")


def mark_schedule_paid(
    session: Session,
    scheduled_id: int,
    *,
    as_of: date | None = None,
    create_transaction: bool = True,
) -> dict[str, Any]:
    """Mark one occurrence of an active expense schedule as paid.

    For active ScheduledItem with amount < 0 (expense):
    - If create_transaction and account_id set: create cleared expense txn on that
      account for the signed expense amount on as_of (or next_date), apply_amount_to_account
    - Advance next_date via next_occurrence(cadence) from next_date
      (or as_of if next_date < as_of)
    - If end_date and new next > end_date: set active=False, ended_reason='paid through end'
    Return {ok, scheduled_id, name, next_date, transaction_id?, ended?}
    """
    row = session.get(ScheduledItem, scheduled_id)
    if not row:
        raise ValueError("Scheduled item not found")
    if not row.active:
        raise ValueError("Scheduled item is not active")

    amount = Decimal(str(row.amount))
    if amount >= ZERO:
        raise ValueError("Mark paid applies only to expense schedules (amount < 0)")

    pay_date = as_of if as_of is not None else row.next_date
    txn_id: int | None = None

    if create_transaction and row.account_id:
        acct = session.get(Account, row.account_id)
        if not acct:
            raise ValueError("Scheduled account not found")
        expense_amt = -abs(amount)
        txn = Transaction(
            profile_id=row.profile_id,
            account_id=row.account_id,
            category_id=row.category_id,
            txn_date=pay_date,
            amount=expense_amt,
            payee=row.name,
            memo=f"Marked paid · schedule #{row.id}",
            status="cleared",
            is_transfer=False,
        )
        session.add(txn)
        apply_amount_to_account(acct, expense_amt)
        session.flush()
        txn_id = txn.id

    base = row.next_date
    if as_of is not None and base < as_of:
        base = as_of
    new_next = next_occurrence(base, row.cadence or "monthly")

    ended = False
    if row.end_date is not None and new_next > row.end_date:
        row.active = False
        row.ended_at = datetime.now()
        row.ended_reason = "paid through end"
        ended = True

    row.next_date = new_next
    session.flush()

    out: dict[str, Any] = {
        "ok": True,
        "scheduled_id": row.id,
        "name": row.name,
        "next_date": new_next.isoformat(),
        "ended": ended,
    }
    if txn_id is not None:
        out["transaction_id"] = txn_id
    return out
