"""Statement cycle windows, projected statement balance, and next payment math.

Pure date/payment helpers plus `project_card_payment` reading Account.
Promo installment lines (Task 6) are stubbed as zero remaining/due for now.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account
from financial_os.engine.ifpp import next_due_date

ZERO = Decimal("0")
CENT = Decimal("0.01")
TWO_PCT = Decimal("0.02")
MIN_FLOOR = Decimal("25")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return _d(v).quantize(CENT)


def _days_in_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def _close_in_month(year: int, month: int, close_day: int) -> date:
    """Close date in year/month; clamp day to month length (28–31 → month end when short)."""
    if close_day < 1:
        close_day = 1
    day = min(close_day, _days_in_month(year, month))
    return date(year, month, day)


def previous_close_date(as_of: date, close_day: int) -> date:
    """Most recent statement close on or before as_of."""
    candidate = _close_in_month(as_of.year, as_of.month, close_day)
    if candidate <= as_of:
        return candidate
    year, month = as_of.year, as_of.month - 1
    if month < 1:
        month = 12
        year -= 1
    return _close_in_month(year, month, close_day)


def next_close_date(as_of: date, close_day: int) -> date:
    """First statement close strictly after as_of."""
    candidate = _close_in_month(as_of.year, as_of.month, close_day)
    if candidate > as_of:
        return candidate
    year, month = as_of.year, as_of.month + 1
    if month > 12:
        month = 1
        year += 1
    return _close_in_month(year, month, close_day)


def compute_next_payment(
    *,
    policy: str,
    current_balance: Decimal,
    min_payment: Decimal | None,
    fixed_amount: Decimal | None,
    promo_remaining: Decimal = ZERO,
    promo_due: Decimal = ZERO,
) -> Decimal:
    """Next cash payment under autopay/pay policy (v1 formula)."""
    policy = (policy or "none").lower().strip()
    bal = _d(current_balance)
    promo_rem = _d(promo_remaining)
    promo_d = _d(promo_due)

    if policy == "statement":
        return _q(max(ZERO, bal - promo_rem) + promo_d)
    if policy == "min":
        if min_payment is not None:
            return _q(_d(min_payment))
        return _q(max(MIN_FLOOR, bal * TWO_PCT))
    if policy == "fixed":
        if fixed_amount is None:
            return ZERO
        return _q(_d(fixed_amount))
    if policy == "promo_sink":
        return _q(promo_d)
    return ZERO


def _promo_totals_stub(
    session: Session,
    account_id: int,
    *,
    as_of: date,
) -> tuple[Decimal, Decimal]:
    """(principal_remaining, monthly_due). Task 6 will sum PromoInstallmentLine rows."""
    _ = (session, account_id, as_of)
    return ZERO, ZERO


def project_card_payment(
    session: Session,
    account_id: int,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Project open-cycle statement and next payment for a credit account.

    Returns statement_balance, next_payment, next_due, last_close, next_close,
    funding_account_id, policy.
    """
    as_of = as_of or date.today()
    acct = session.get(Account, account_id)
    if not acct:
        raise ValueError(f"Account {account_id} not found")

    close_day = int(acct.statement_close_day) if acct.statement_close_day else 1
    if close_day < 1:
        close_day = 1
    if close_day > 31:
        close_day = 31

    last_close = previous_close_date(as_of, close_day)
    next_close = next_close_date(as_of, close_day)
    next_due = next_due_date(as_of, acct.payment_due_day)

    policy = (acct.autopay_policy or "none").lower().strip()
    bal = _d(acct.current_balance)
    promo_remaining, promo_due = _promo_totals_stub(session, account_id, as_of=as_of)

    statement_balance = _q(max(ZERO, bal - promo_remaining))
    next_payment = compute_next_payment(
        policy=policy,
        current_balance=bal,
        min_payment=_d(acct.min_payment) if acct.min_payment is not None else None,
        fixed_amount=_d(acct.payment_fixed_amount)
        if acct.payment_fixed_amount is not None
        else None,
        promo_remaining=promo_remaining,
        promo_due=promo_due,
    )

    return {
        "statement_balance": statement_balance,
        "next_payment": next_payment,
        "next_due": next_due,
        "last_close": last_close,
        "next_close": next_close,
        "funding_account_id": acct.payment_funding_account_id,
        "policy": policy,
    }
