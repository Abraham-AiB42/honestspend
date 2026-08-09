"""Void / reverse a transaction without hard-deleting history."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, Transaction

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def void_transaction(
    session: Session,
    txn_id: int,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Reverse balance impact and mark status=void.

    Idempotent if already void. Does not remove the row.
    """
    row = session.get(Transaction, txn_id)
    if not row:
        raise ValueError("Transaction not found")
    if (row.status or "").lower() == "void":
        return {
            "ok": True,
            "transaction_id": row.id,
            "status": "void",
            "already_void": True,
        }

    acct = session.get(Account, row.account_id)
    amt = _d(row.amount)

    if acct and (row.status or "cleared").lower() != "void":
        from financial_os.services.account_balance import reverse_amount_on_account

        reverse_amount_on_account(acct, amt)

    row.status = "void"
    note = (row.memo or "").strip()
    stamp = datetime.now().isoformat(timespec="seconds")
    void_note = f"[voided {stamp}" + (f": {reason}" if reason else "") + "]"
    row.memo = f"{note} {void_note}".strip() if note else void_note
    session.flush()

    return {
        "ok": True,
        "transaction_id": row.id,
        "status": "void",
        "already_void": False,
        "account_id": row.account_id,
        "reversed_amount": str(amt),
        "account_balance": str(_d(acct.current_balance)) if acct else None,
    }
