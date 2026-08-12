"""Rebuild / verify Account.current_balance from the transaction ledger.

Sums book-affecting transactions (excludes void and pending) with the same
sign rules as `apply_amount_to_account` (cash: += amount; credit: -= amount).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Transaction

ZERO = Decimal("0")
TWOPLACES = Decimal("0.01")

# Statuses that do not affect books (live apply skips pending; void is reversed).
_NON_BOOK_STATUSES = frozenset({"void", "pending"})


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _quantize(v: Decimal) -> Decimal:
    return _d(v).quantize(TWOPLACES)


def _is_non_book(status: str | None) -> bool:
    """True if status does not affect books (void or pending; case-insensitive)."""
    return (status or "").lower() in _NON_BOOK_STATUSES


def _contribution(account_kind: str, amount: Decimal | Any) -> Decimal:
    """Balance delta for one txn under apply_amount_to_account rules."""
    amt = _d(amount)
    if account_kind != "credit":
        return amt
    return -amt


def _sum_book_balance(session: Session, account: Account) -> Decimal:
    """Sum book-affecting txn contributions for account (does not write).

    Excludes void and pending (case-insensitive); matches live apply behavior.
    """
    kind = account.kind or ""
    rows = (
        session.query(Transaction)
        .filter(Transaction.account_id == account.id)
        .all()
    )
    total = ZERO
    for row in rows:
        if _is_non_book(row.status):
            continue
        total += _contribution(kind, row.amount)
    return _quantize(total)


def rebuild_running_balance(session: Session, account_id: int) -> Decimal:
    """Sum book-affecting txns; write Account.current_balance.

    Uses the same sign rules as apply_amount_to_account and excludes void and
    pending (case-insensitive), matching live apply which does not book pending.

    Rebuild starts from zero (ledger sum only). Bare opening balances without a
    seed txn are overwritten.
    """
    account = session.get(Account, account_id)
    if account is None:
        raise ValueError(f"Account not found: {account_id}")

    rebuilt = _sum_book_balance(session, account)
    account.current_balance = rebuilt
    if account.kind == "credit" and account.credit_limit is not None:
        account.available_credit = _quantize(_d(account.credit_limit) - rebuilt)
    session.flush()
    # Credit books drive statement projection + cash Card payment schedules
    if (account.kind or "") == "credit":
        from honestspend.services.autopay import after_account_balance_changed

        after_account_balance_changed(session, int(account.id))
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
    rebuilt = _sum_book_balance(session, account)
    delta = _quantize(books - rebuilt)
    return {
        "books": books,
        "rebuilt": rebuilt,
        "delta": delta,
        "ok": delta == ZERO,
    }
