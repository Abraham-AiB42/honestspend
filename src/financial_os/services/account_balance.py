"""Apply signed transaction amounts to Account.current_balance (books).

Shared by manual POST /transactions, CSV/OFX/PDF import, and void reverse.
IFPP Safe to spend reads current_balance — imports must call this for honesty.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_os.db import Account

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def apply_amount_to_account(account: Account, amount: Decimal | Any) -> None:
    """Mirror create_transaction balance rules.

    Cash/savings/etc: balance += amount (expenses negative).
    Credit: balance owed -= amount (charge amount is negative → owed increases).
    """
    amt = _d(amount)
    if account.kind != "credit":
        account.current_balance = _d(account.current_balance) + amt
    else:
        account.current_balance = _d(account.current_balance) - amt
        if account.credit_limit is not None:
            bal = _d(account.current_balance)
            account.available_credit = _d(account.credit_limit) - bal


def reverse_amount_on_account(account: Account, amount: Decimal | Any) -> None:
    """Undo apply_amount_to_account (void path)."""
    amt = _d(amount)
    if account.kind != "credit":
        account.current_balance = _d(account.current_balance) - amt
    else:
        account.current_balance = _d(account.current_balance) + amt
        if account.credit_limit is not None:
            bal = _d(account.current_balance)
            account.available_credit = _d(account.credit_limit) - bal
