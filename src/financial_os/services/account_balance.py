"""Apply signed transaction amounts to Account.current_balance (books).

Shared by manual POST /transactions, CSV/OFX/PDF import, and void reverse.
IFPP Safe to spend reads current_balance — imports must call this for honesty.

After credit balance changes, recompute cash-funded card payment schedules.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import object_session

from financial_os.db import Account

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _session_for(account: Account, session: Any | None) -> Any | None:
    if session is not None:
        return session
    try:
        return object_session(account)
    except Exception:
        return None


def _maybe_recompute_credit(account: Account, session: Any | None) -> None:
    """After credit books change, refresh statement caches + Card payment schedule."""
    if (account.kind or "") != "credit" or account.id is None:
        return
    sess = _session_for(account, session)
    if sess is None:
        return
    from financial_os.services.autopay import after_account_balance_changed

    after_account_balance_changed(sess, int(account.id))


def apply_amount_to_account(
    account: Account,
    amount: Decimal | Any,
    *,
    confirm_unsafe: bool = False,
    session: Any | None = None,
) -> None:
    """Mirror create_transaction balance rules.

    Cash/savings/etc: balance += amount (expenses negative).
    Credit: balance owed -= amount (charge amount is negative → owed increases).

    When session is provided and amount would push checking negative, runs never-neg.
    Credit accounts also recompute next payment / cash schedule when a session is
    available (passed in or via SQLAlchemy object_session).
    """
    amt = _d(amount)
    if session is not None and amt < ZERO and (account.kind or "") == "checking":
        from financial_os.services.never_neg import check_cash_outflow

        check_cash_outflow(
            session,
            account=account,
            amount=amt,
            confirm_unsafe=confirm_unsafe,
        )
    if account.kind != "credit":
        account.current_balance = _d(account.current_balance) + amt
    else:
        account.current_balance = _d(account.current_balance) - amt
        if account.credit_limit is not None:
            bal = _d(account.current_balance)
            account.available_credit = _d(account.credit_limit) - bal
        _maybe_recompute_credit(account, session)


def reverse_amount_on_account(
    account: Account,
    amount: Decimal | Any,
    *,
    session: Any | None = None,
) -> None:
    """Undo apply_amount_to_account (void path)."""
    amt = _d(amount)
    if account.kind != "credit":
        account.current_balance = _d(account.current_balance) - amt
    else:
        account.current_balance = _d(account.current_balance) + amt
        if account.credit_limit is not None:
            bal = _d(account.current_balance)
            account.available_credit = _d(account.credit_limit) - bal
        _maybe_recompute_credit(account, session)
