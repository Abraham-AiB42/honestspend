"""Promo death clock + sinking fund schedule for 0% balances."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account
from financial_os.engine.ifpp import CardView
from financial_os.engine.schedule_expand import add_months
from financial_os.services.payoff import plan_card_payoff

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def promo_death_clock(session: Session, *, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    cards = session.query(Account).filter(Account.kind == "credit").all()
    items = []
    for a in cards:
        if a.promo_end_date is None:
            continue
        if a.promo_apr is None or _d(a.promo_apr) != ZERO:
            continue
        days = (a.promo_end_date - as_of).days
        bal = max(ZERO, _d(a.promo_balance if a.promo_balance is not None else a.current_balance))
        if bal <= ZERO and days < 0:
            continue
        # Sinking fund: linear set-aside from today to promo end
        months_left = max(1, (days // 30) or 1)
        if days > 0:
            monthly_sink = (bal / Decimal(months_left)).quantize(Decimal("0.01"))
            weekly_sink = (bal / Decimal(max(1, days // 7 or 1))).quantize(Decimal("0.01"))
        else:
            monthly_sink = bal
            weekly_sink = bal

        urgency = "ok"
        if days < 0:
            urgency = "expired"
        elif days <= 14:
            urgency = "critical"
        elif days <= 45:
            urgency = "soon"
        elif days <= 90:
            urgency = "watch"

        cv = CardView(
            id=a.id,
            name=a.nickname,
            balance=_d(a.current_balance),
            credit_limit=_d(a.credit_limit),
            available_credit=_d(a.available_credit),
            statement_close_day=a.statement_close_day,
            payment_due_day=a.payment_due_day,
            apr=Decimal(a.apr) if a.apr is not None else None,
            promo_apr=Decimal(a.promo_apr) if a.promo_apr is not None else None,
            promo_end_date=a.promo_end_date,
            promo_balance=Decimal(a.promo_balance) if a.promo_balance is not None else None,
            min_payment=Decimal(a.min_payment) if a.min_payment is not None else None,
        )
        plan = plan_card_payoff(cv, as_of=as_of)

        items.append(
            {
                "account_id": a.id,
                "name": a.nickname,
                "promo_end": a.promo_end_date.isoformat(),
                "days_left": days,
                "urgency": urgency,
                "promo_balance": str(bal),
                "sinking_fund": {
                    "monthly": str(monthly_sink),
                    "weekly": str(weekly_sink),
                    "note": "Park this much so balloon doesn't hit checking as a surprise",
                },
                "payoff_summary": plan.summary,
                "strategy": plan.strategy,
            }
        )

    items.sort(key=lambda x: x["days_left"])
    return {
        "as_of": as_of.isoformat(),
        "count": len(items),
        "items": items,
        "principle": "Intentional 0% float is OK — death clock ensures you never convert to APR by accident.",
    }
