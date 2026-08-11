"""Statement cycle windows, projected statement balance, and next payment math.

Pure date/payment helpers plus `project_card_payment` reading Account.
Promo installment lines reduce statement balance and add monthly_due to payment.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account
from financial_os.engine.ifpp import next_due_date
from financial_os.services.promo_installments import open_promo_totals

ZERO = Decimal("0")
CENT = Decimal("0.01")
TWO_PCT = Decimal("0.02")
MIN_FLOOR = Decimal("25")
TIMINGS = frozenset({"on_due", "on_close", "day_before_close"})


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


def close_on_or_after(as_of: date, close_day: int) -> date:
    """Next statement close on or after as_of (pay-on-close day can be today)."""
    candidate = _close_in_month(as_of.year, as_of.month, close_day)
    if candidate >= as_of:
        return candidate
    year, month = as_of.year, as_of.month + 1
    if month > 12:
        month = 1
        year += 1
    return _close_in_month(year, month, close_day)


def day_before(d: date) -> date:
    return d - timedelta(days=1)


def normalize_timing(timing: str | None) -> str:
    t = (timing or "on_due").lower().strip()
    return t if t in TIMINGS else "on_due"


def next_payment_date(
    *,
    as_of: date,
    timing: str | None,
    close_day: int | None,
    due_day: int | None,
) -> date | None:
    """Effective cash payment date from timing + close/due days."""
    t = normalize_timing(timing)
    if t == "on_due":
        return next_due_date(as_of, due_day)
    # Close-based
    if not close_day or close_day < 1:
        return next_due_date(as_of, due_day)
    close_day = min(31, int(close_day))
    close = close_on_or_after(as_of, close_day)
    if t == "on_close":
        return close
    # day_before_close
    return day_before(close)


def compute_next_payment(
    *,
    policy: str,
    current_balance: Decimal,
    min_payment: Decimal | None,
    fixed_amount: Decimal | None,
    promo_remaining: Decimal = ZERO,
    promo_due: Decimal = ZERO,
) -> Decimal:
    """Next cash payment under autopay/pay policy."""
    policy = (policy or "none").lower().strip()
    bal = _d(current_balance)
    promo_rem = _d(promo_remaining)
    promo_d = _d(promo_due)

    if policy == "books":
        # Full books — zero-statement strategy (includes promo principal)
        return _q(max(ZERO, bal))
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


def project_card_payment(
    session: Session,
    account_id: int,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Project open-cycle statement and next payment for a credit account.

    Returns statement_balance, next_payment, next_due (effective pay date),
    last_close, next_close, funding_account_id, policy, payment_timing,
    promo_remaining, promo_due, warnings.
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
    timing = normalize_timing(getattr(acct, "payment_timing", None))
    pay_date = next_payment_date(
        as_of=as_of,
        timing=timing,
        close_day=acct.statement_close_day or close_day,
        due_day=acct.payment_due_day,
    )
    # Fallback: due day or next close
    if pay_date is None:
        pay_date = next_due_date(as_of, acct.payment_due_day) or next_close

    policy = (acct.autopay_policy or "none").lower().strip()
    bal = _d(acct.current_balance)
    promo_remaining, promo_due = open_promo_totals(session, account_id, as_of=as_of)

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

    warnings: list[str] = []
    if policy == "books" and promo_remaining > ZERO:
        warnings.append(
            "Pay current balance includes open promo principal and may end 0% float early. "
            "Prefer Pay statement in full on due day to keep installment carve-outs."
        )
    if timing in ("on_close", "day_before_close") and not acct.statement_close_day:
        warnings.append("Set statement close day for close-based pay timing.")

    return {
        "statement_balance": statement_balance,
        "next_payment": next_payment,
        "next_due": pay_date,
        "last_close": last_close,
        "next_close": next_close,
        "funding_account_id": acct.payment_funding_account_id,
        "policy": policy,
        "payment_timing": timing,
        "promo_remaining": promo_remaining,
        "promo_due": promo_due,
        "warnings": warnings,
    }
