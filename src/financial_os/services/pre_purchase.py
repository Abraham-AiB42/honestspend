"""Pre-purchase check: can I buy this without going neg or paying interest?"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account
from financial_os.engine.ifpp import CardView, recommend_card_for_purchase
from financial_os.services.ifpp_service import run_ifpp
from financial_os.services.payoff import plan_card_payoff

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


@dataclass
class PurchaseOption:
    method: str  # cash | card
    account_id: int | None
    account_name: str
    safe: bool
    reason: str
    remaining_spendable: Decimal | None = None
    card_plan_summary: str | None = None


def check_purchase(
    session: Session,
    *,
    amount: Decimal,
    prefer: str = "auto",  # auto | cash | card
    account_id: int | None = None,
    as_of: date | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    amount = abs(_d(amount))
    ifpp = run_ifpp(session, as_of=as_of, profile_id=profile_id, scope=scope)
    options: list[PurchaseOption] = []

    # Cash path
    cash_ok = ifpp.cash_spendable >= amount
    options.append(
        PurchaseOption(
            method="cash",
            account_id=None,
            account_name="Checking / IFPP cash pool",
            safe=cash_ok,
            reason=(
                f"Cash spendable ${ifpp.cash_spendable} "
                + ("covers this purchase." if cash_ok else "is short — would risk never-neg checking rule.")
            ),
            remaining_spendable=(ifpp.cash_spendable - amount) if cash_ok else ifpp.cash_spendable,
        )
    )

    # Card paths (entity-scoped when IFPP is entity)
    cq = session.query(Account).filter(Account.kind == "credit", Account.archived_at.is_(None))
    sc = (ifpp.details or {}).get("ifpp_scope") or scope or "entity"
    pid = (ifpp.details or {}).get("profile_id")
    if sc == "entity" and pid is not None:
        cq = cq.filter(Account.profile_id == pid)
    accounts = cq.all()
    card_views: list[CardView] = []
    for a in accounts:
        if account_id and a.id != account_id:
            continue
        cv = CardView(
            id=a.id,
            name=a.nickname,
            balance=Decimal(a.current_balance or 0),
            credit_limit=Decimal(a.credit_limit or 0),
            available_credit=Decimal(a.available_credit or 0),
            statement_close_day=a.statement_close_day,
            payment_due_day=a.payment_due_day,
            apr=Decimal(a.apr) if a.apr is not None else None,
            promo_apr=Decimal(a.promo_apr) if a.promo_apr is not None else None,
            promo_end_date=a.promo_end_date,
            promo_balance=Decimal(a.promo_balance) if a.promo_balance is not None else None,
            min_payment=Decimal(a.min_payment) if a.min_payment is not None else None,
            rewards_score=1 if a.rewards_program else 0,
        )
        card_views.append(cv)

    # Use IFPP card plans
    card_plans = {c.account_id: c for c in ifpp.cards}
    for cv in card_views:
        plan = card_plans.get(cv.id)
        safe_amt = plan.safe_to_charge if plan else ZERO
        safe = safe_amt >= amount
        payoff = plan_card_payoff(cv, as_of=as_of)
        reason = (
            f"Interest-free path: can charge ${safe_amt}. {plan.payoff_path if plan else ''}"
            if plan
            else "No IFPP plan for card."
        )
        if not safe:
            reason = (
                f"Not safe: only ${safe_amt} interest-free capacity. "
                f"Charging ${amount} risks revolving/APR. {plan.payoff_path if plan else ''}"
            )
        options.append(
            PurchaseOption(
                method="card",
                account_id=cv.id,
                account_name=cv.name,
                safe=safe,
                reason=reason.strip(),
                remaining_spendable=safe_amt - amount if safe else safe_amt,
                card_plan_summary=payoff.summary,
            )
        )

    # Recommendation
    rec = None
    if prefer == "cash":
        rec = next((o for o in options if o.method == "cash"), None)
    elif prefer == "card" and account_id:
        rec = next((o for o in options if o.account_id == account_id), None)
    elif prefer == "card":
        safe_cards = [o for o in options if o.method == "card" and o.safe]
        rec = safe_cards[0] if safe_cards else next((o for o in options if o.method == "card"), None)
    else:
        # auto: prefer interest-free card if safe (float strategy), else cash if safe
        safe_cards = [o for o in options if o.method == "card" and o.safe]
        cash = next(o for o in options if o.method == "cash")
        if safe_cards:
            # fiscal: 0% float OK when path exists — prefer card to preserve cash yield
            rec = safe_cards[0]
        elif cash.safe:
            rec = cash
        else:
            rec = cash  # unsafe but show it

    verdict = "do_not_buy"
    if rec and rec.safe:
        verdict = "safe"
    elif any(o.safe for o in options):
        verdict = "safe_via_other_method"

    return {
        "amount": str(amount),
        "as_of": as_of.isoformat(),
        "verdict": verdict,
        "recommended": {
            "method": rec.method if rec else None,
            "account_id": rec.account_id if rec else None,
            "account_name": rec.account_name if rec else None,
            "safe": rec.safe if rec else False,
            "reason": rec.reason if rec else "No path",
            "remaining_after": str(rec.remaining_spendable) if rec and rec.remaining_spendable is not None else None,
        },
        "options": [
            {
                "method": o.method,
                "account_id": o.account_id,
                "account_name": o.account_name,
                "safe": o.safe,
                "reason": o.reason,
                "remaining_after": str(o.remaining_spendable) if o.remaining_spendable is not None else None,
                "card_plan_summary": o.card_plan_summary,
            }
            for o in options
        ],
        "ifpp_snapshot": {
            "cash_spendable": str(ifpp.cash_spendable),
            "card_float_interest_free": str(ifpp.card_float_interest_free),
            "next_red_day": ifpp.next_red_day.isoformat() if ifpp.next_red_day else None,
        },
        "principles": [
            "Never go negative on checking",
            "Intentional 0% float OK when full interest-free payoff path exists",
            "Do not charge if it forces revolving APR",
        ],
    }
