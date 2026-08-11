"""Rebuild / verify Account.current_balance from the transaction ledger.

Sums non-void transactions with the same sign rules as
`apply_amount_to_account` (cash: += amount; credit: -= amount).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, Transaction

ZERO = Decimal("0")
TWOPLACES = Decimal("0.01")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _quantize(v: Decimal) -> Decimal:
    return _d(v).quantize(TWOPLACES)


def _is_void(status: str | None) -> bool:
    return (status or "").lower() == "void"


def _contribution(account_kind: str, amount: Decimal | Any) -> Decimal:
    """Balance delta for one txn under apply_amount_to_account rules."""
    amt = _d(amount)
    if account_kind != "credit":
        return amt
    return -amt


def _sum_non_void_balance(session: Session, account: Account) -> Decimal:
    """Sum non-void txn contributions for account (does not write)."""
    kind = account.kind or ""
    rows = (
        session.query(Transaction)
        .filter(Transaction.account_id == account.id)
        .all()
    )
    total = ZERO
    for row in rows:
        if _is_void(row.status):
            continue
        total += _contribution(kind, row.amount)
    return _quantize(total)


def rebuild_running_balance(session: Session, account_id: int) -> Decimal:
    """Sum non-void txns with same sign rules as apply_amount_to_account; write Account.current_balance."""
    account = session.get(Account, account_id)
    if account is None:
        raise ValueError(f"Account not found: {account_id}")

    rebuilt = _sum_non_void_balance(session, account)
    account.current_balance = rebuilt
    if account.kind == "credit" and account.credit_limit is not None:
        account.available_credit = _quantize(_d(account.credit_limit) - rebuilt)
    session.flush()
    return rebuilt


def verify_running_balance(session: Session, account_id: int) -> dict:
    """Compare books current_balance to ledger-summed rebuilt balance.

    Returns dict with books, rebuilt, delta (books - rebuilt), ok.
    Does not write Account.current_balance.
    """
    account = session.get(Account, account_id)
    if account is None:
        raise ValueError(f"Account not found: {account_id}")

    books = _quantize(_d(account.current_balance))
    rebuilt = _sum_non_void_balance(session, account)
    delta = _quantize(books - rebuilt)
    return {
        "books": books,
        "rebuilt": rebuilt,
        "delta": delta,
        "ok": delta == ZERO,
    }
