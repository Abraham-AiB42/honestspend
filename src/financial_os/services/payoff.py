"""Credit card promo / statement payoff planner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from financial_os.engine.ifpp import CardView, is_promo_active, next_due_date
from financial_os.engine.schedule_expand import add_months

ZERO = Decimal("0")


@dataclass
class PayoffPlan:
    account_id: int
    name: str
    strategy: str
    balance: Decimal
    promo_balance: Decimal
    promo_end: date | None
    next_due: date | None
    steps: list[dict[str, Any]] = field(default_factory=list)
    monthly_min_total: Decimal = ZERO
    balloon_amount: Decimal = ZERO
    balloon_date: date | None = None
    interest_if_followed: Decimal = ZERO
    warnings: list[str] = field(default_factory=list)
    summary: str = ""


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def plan_card_payoff(card: CardView, *, as_of: date | None = None) -> PayoffPlan:
    as_of = as_of or date.today()
    balance = max(ZERO, _d(card.balance))
    promo_bal = _d(card.promo_balance) if card.promo_balance is not None else ZERO
    if promo_bal <= ZERO and is_promo_active(card, as_of):
        promo_bal = balance  # assume whole balance is promo if tagged 0%
    min_pay = _d(card.min_payment) if card.min_payment is not None else max(
        Decimal("25"), (balance * Decimal("0.02")).quantize(Decimal("0.01"))
    )
    due = next_due_date(as_of, card.payment_due_day)
    warnings: list[str] = []
    steps: list[dict[str, Any]] = []

    if balance <= ZERO:
        return PayoffPlan(
            account_id=card.id,
            name=card.name,
            strategy="paid_off",
            balance=ZERO,
            promo_balance=ZERO,
            promo_end=card.promo_end_date,
            next_due=due,
            summary="No balance — nothing to pay.",
        )

    if is_promo_active(card, as_of) and card.promo_end_date:
        # Min until last month of promo, then balloon
        promo_end = card.promo_end_date
        cursor = due or as_of
        total_mins = ZERO
        remaining = promo_bal if promo_bal > ZERO else balance
        guard = 0
        while cursor < promo_end and guard < 48:
            # last payment month: balloon
            next_due_d = next_due_date(cursor + __import__("datetime").timedelta(days=1), card.payment_due_day)
            # If next due is after or on promo end month, this is balloon cycle
            if next_due_d is None or next_due_d > promo_end or (
                next_due_d.year == promo_end.year and next_due_d.month == promo_end.month
            ):
                break
            pay = min(min_pay, remaining)
            if pay <= ZERO:
                break
            steps.append(
                {
                    "date": cursor.isoformat(),
                    "action": "minimum_payment",
                    "amount": str(pay),
                    "note": "0% promo — pay minimum only",
                }
            )
            remaining -= pay
            total_mins += pay
            cursor = next_due_d or add_months(cursor, 1)
            guard += 1

        balloon = remaining
        balloon_date = next_due_date(promo_end.replace(day=1), card.payment_due_day) or promo_end
        if balloon_date > promo_end:
            # pay before promo ends
            balloon_date = promo_end
        steps.append(
            {
                "date": balloon_date.isoformat(),
                "action": "balloon_payoff",
                "amount": str(balloon.quantize(Decimal("0.01"))),
                "note": "Pay remaining promo balance before 0% ends — never revolve at APR",
            }
        )
        if balloon > ZERO and due:
            warnings.append(
                f"Set aside ~${balloon:.2f} by {balloon_date.isoformat()} for promo payoff"
            )
        non_promo = max(ZERO, balance - promo_bal)
        if non_promo > ZERO:
            warnings.append(
                f"${non_promo:.2f} may not be on 0% — pay that portion in full each statement"
            )
            steps.insert(
                0,
                {
                    "date": (due or as_of).isoformat(),
                    "action": "pay_non_promo",
                    "amount": str(non_promo.quantize(Decimal("0.01"))),
                    "note": "Non-promo balance — pay in full to avoid APR",
                },
            )

        return PayoffPlan(
            account_id=card.id,
            name=card.name,
            strategy="promo_min_then_balloon",
            balance=balance,
            promo_balance=promo_bal,
            promo_end=promo_end,
            next_due=due,
            steps=steps,
            monthly_min_total=total_mins.quantize(Decimal("0.01")),
            balloon_amount=balloon.quantize(Decimal("0.01")),
            balloon_date=balloon_date,
            interest_if_followed=ZERO,
            warnings=warnings,
            summary=(
                f"0% until {promo_end.isoformat()}: pay mins (~${min_pay}/cycle), "
                f"then balloon ${balloon:.2f} by {balloon_date.isoformat()}. Interest if followed: $0."
            ),
        )

    # Standard: always pay statement balance in full
    if due is None:
        warnings.append("Set payment due day on this card to plan payoff")
    steps.append(
        {
            "date": (due or as_of).isoformat(),
            "action": "pay_in_full",
            "amount": str(balance.quantize(Decimal("0.01"))),
            "note": "Pay statement balance in full — no interest",
        }
    )
    return PayoffPlan(
        account_id=card.id,
        name=card.name,
        strategy="pay_in_full",
        balance=balance,
        promo_balance=ZERO,
        promo_end=None,
        next_due=due,
        steps=steps,
        balloon_amount=balance.quantize(Decimal("0.01")),
        balloon_date=due,
        interest_if_followed=ZERO,
        warnings=warnings,
        summary=(
            f"Pay ${balance:.2f} in full by {(due or as_of).isoformat()} — no interest."
        ),
    )


def plan_to_dict(plan: PayoffPlan) -> dict[str, Any]:
    return {
        "account_id": plan.account_id,
        "name": plan.name,
        "strategy": plan.strategy,
        "balance": str(plan.balance),
        "promo_balance": str(plan.promo_balance),
        "promo_end": plan.promo_end.isoformat() if plan.promo_end else None,
        "next_due": plan.next_due.isoformat() if plan.next_due else None,
        "steps": plan.steps,
        "monthly_min_total": str(plan.monthly_min_total),
        "balloon_amount": str(plan.balloon_amount),
        "balloon_date": plan.balloon_date.isoformat() if plan.balloon_date else None,
        "interest_if_followed": str(plan.interest_if_followed),
        "warnings": plan.warnings,
        "summary": plan.summary,
    }
