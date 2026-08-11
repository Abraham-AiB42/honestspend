"""Card payment honesty: utilization, estimated interest, educational credit advice.

Educational only — not a FICO/Vantage score. Plain language for API/UI.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_os.db import Account

ZERO = Decimal("0")
CENT = Decimal("0.01")
TWO_PCT = Decimal("0.02")
MIN_FLOOR = Decimal("25")
HUNDRED = Decimal("100")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return _d(v).quantize(CENT)


def estimated_min_payment(
    balance: Decimal | Any,
    min_payment: Decimal | Any | None,
) -> Decimal:
    """Issuer min if set, else max(25, 2% × books)."""
    if min_payment is not None:
        return _q(_d(min_payment))
    bal = max(ZERO, _d(balance))
    return _q(max(MIN_FLOOR, bal * TWO_PCT))


def estimated_monthly_interest(
    balance: Decimal | Any,
    apr: Decimal | Any | None,
) -> Decimal:
    """Simple monthly interest estimate: (APR/12) × balance. Zero if no APR."""
    if apr is None:
        return ZERO
    rate = _d(apr)
    if rate <= ZERO:
        return ZERO
    # APR may be stored as 0.229 or 22.9 — treat > 1 as percent
    if rate > 1:
        rate = rate / HUNDRED
    bal = max(ZERO, _d(balance))
    return _q(bal * rate / Decimal("12"))


def utilization_pct(
    balance: Decimal | Any,
    limit: Decimal | Any | None,
) -> Decimal | None:
    """Books / limit as percent (0–100+), or None if no limit."""
    lim = _d(limit) if limit is not None else ZERO
    if lim <= ZERO:
        return None
    bal = max(ZERO, _d(balance))
    return _q((bal / lim) * HUNDRED)


def amount_to_utilization_target(
    balance: Decimal | Any,
    limit: Decimal | Any | None,
    target_pct: int,
) -> Decimal:
    """Cash paydown needed so balance ≤ limit × target_pct/100."""
    lim = _d(limit) if limit is not None else ZERO
    if lim <= ZERO:
        return ZERO
    bal = max(ZERO, _d(balance))
    target = lim * Decimal(int(target_pct)) / HUNDRED
    return _q(max(ZERO, bal - target))


def credit_advice_for_policy(
    policy: str,
    timing: str | None,
    *,
    has_promo: bool = False,
) -> list[str]:
    """Educational bullets — estimates / typical issuer behavior, not a score."""
    policy = (policy or "none").lower().strip()
    timing = (timing or "on_due").lower().strip()
    tips: list[str] = []

    tips.append(
        "Many issuers report a balance near statement close. Paying down before "
        "that date can mean lower reported utilization — often a plus for score models."
    )
    tips.append(
        "Low utilization is usually better than high. All cards at $0 is not required; "
        "some models like seeing a little activity with low balances."
    )
    tips.append(
        "HonestSpend does not calculate a FICO or Vantage score. This is general education, "
        "not a prediction of your score."
    )

    if policy == "books":
        tips.insert(
            0,
            "Pay current balance uses your full books balance (including any promo principal) "
            "so the statement can open near $0 if the bank posts your payment in time.",
        )
        if timing in ("on_close", "day_before_close"):
            tips.insert(
                1,
                "Cash leaves on or just before statement close — earlier than a typical due day. "
                "Safe to spend reserves that cash sooner; you give up statement float.",
            )
        if has_promo:
            tips.insert(
                1,
                "Open 0% promo lines: paying full books may end promo principal early. "
                "Prefer “Pay statement in full” on due day if you want to keep installments.",
            )
    elif policy == "statement":
        tips.append(
            "Pay statement in full keeps intentional 0% float: promo principal stays carved out; "
            "you still pay this cycle’s promo installments."
        )
    elif policy == "min":
        tips.append(
            "Minimum only can leave a large balance into interest after any promo ends. "
            "Check estimated monthly interest on this card."
        )

    return tips


def honesty_payload(
    account: Account,
    *,
    promo_remaining: Decimal = ZERO,
    soft_pct: int = 10,
    hard_pct: int = 30,
    policy: str | None = None,
    timing: str | None = None,
) -> dict[str, Any]:
    """JSON-ready honesty block for cycle API."""
    bal = _d(account.current_balance)
    lim = account.credit_limit
    pol = (policy or account.autopay_policy or "none").lower().strip()
    tim = timing if timing is not None else getattr(account, "payment_timing", None)
    min_est = estimated_min_payment(bal, account.min_payment)
    interest = estimated_monthly_interest(bal, account.apr)
    util = utilization_pct(bal, lim)
    to_soft = amount_to_utilization_target(bal, lim, soft_pct)
    to_hard = amount_to_utilization_target(bal, lim, hard_pct)
    has_promo = _d(promo_remaining) > ZERO
    advice = credit_advice_for_policy(pol, tim, has_promo=has_promo)

    min_source = "issuer" if account.min_payment is not None else "estimated"
    return {
        "balance": str(_q(bal)),
        "min_payment": str(min_est),
        "min_payment_source": min_source,
        "apr": str(account.apr) if account.apr is not None else None,
        "estimated_monthly_interest_if_min": str(interest),
        "utilization_pct": str(util) if util is not None else None,
        "credit_limit": str(_q(_d(lim))) if lim is not None else None,
        "amount_to_util_soft": str(to_soft),
        "amount_to_util_hard": str(to_hard),
        "util_soft_pct": soft_pct,
        "util_hard_pct": hard_pct,
        "advice": advice,
        "disclaimer": (
            "Educational estimates only. Not a credit score. Issuer reporting dates vary."
        ),
    }
