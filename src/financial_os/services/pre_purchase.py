"""Pre-purchase check: can I buy this without going neg or paying interest?"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, Category
from financial_os.engine.ifpp import CardView
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


def _budget_check(
    session: Session,
    *,
    profile_id: int | None,
    category_id: int,
    amount: Decimal,
    as_of: date,
) -> dict[str, Any]:
    """Match period-budget remaining for a category (prefer daily if multiple)."""
    from financial_os.services.budget_service import budgets_status

    st = budgets_status(session, profile_id=profile_id, as_of=as_of)
    match: dict[str, Any] | None = None
    for it in st.get("items") or []:
        if int(it.get("category_id") or 0) != int(category_id):
            continue
        match = it
        if it.get("period") == "daily":
            break
    cat = session.get(Category, category_id)
    cat_name = cat.display_name if cat else f"Category {category_id}"
    if not match:
        return {
            "category_id": category_id,
            "category_name": cat_name,
            "has_rule": False,
            "ok": True,
            "message": f"No budget for {cat_name} — only Safe to spend applies.",
        }
    rem = _d(match.get("remaining"))
    plan = _d(match.get("plan"))
    period = str(match.get("period") or "")
    ok = amount <= rem
    if ok:
        msg = (
            f"Budget ok: {cat_name} ({period}) has ${rem} left of ${plan}; "
            f"this ${amount} still fits."
        )
    else:
        msg = (
            f"Budget short: {cat_name} ({period}) has ${rem} left of ${plan}. "
            f"This purchase needs ${amount}. Cut a budget or wait for the next period."
        )
    return {
        "category_id": category_id,
        "category_name": cat_name,
        "has_rule": True,
        "ok": ok,
        "period": period,
        "plan": str(plan),
        "remaining": str(rem),
        "status": match.get("status"),
        "message": msg,
    }


def check_purchase(
    session: Session,
    *,
    amount: Decimal,
    prefer: str = "auto",  # auto | cash | card
    account_id: int | None = None,
    as_of: date | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
    category_id: int | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    amount = abs(_d(amount))
    ifpp = run_ifpp(session, as_of=as_of, profile_id=profile_id, scope=scope)
    options: list[PurchaseOption] = []

    # Align with Home: Safe to spend = cash_spendable − period budget reserve
    from financial_os.services.budget_service import budget_reserve_total

    reserve = budget_reserve_total(session, profile_id=profile_id, as_of=as_of)
    cash_raw = _d(ifpp.cash_spendable)
    safe_to_spend = max(ZERO, cash_raw - reserve)

    # Cash path (never-neg + budget reserve)
    cash_ok = safe_to_spend >= amount
    if cash_ok:
        cash_reason = f"Safe to spend ${safe_to_spend} covers this purchase."
        if reserve > 0:
            cash_reason += f" (${cash_raw} cash − ${reserve} budget reserve)."
    else:
        cash_reason = (
            f"Safe to spend ${safe_to_spend} is short — would risk never-neg "
            f"or raid reserved budgets (${cash_raw} cash, ${reserve} reserved)."
        )
    options.append(
        PurchaseOption(
            method="cash",
            account_id=None,
            account_name="Checking / Safe to spend",
            safe=cash_ok,
            reason=cash_reason,
            remaining_spendable=(safe_to_spend - amount) if cash_ok else safe_to_spend,
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

    budget_check: dict[str, Any] | None = None
    if category_id:
        budget_check = _budget_check(
            session,
            profile_id=profile_id,
            category_id=int(category_id),
            amount=amount,
            as_of=as_of,
        )
        # Soft gate: liquidity OK but envelope short → still surface as tight
        if (
            budget_check.get("has_rule")
            and not budget_check.get("ok")
            and verdict in ("safe", "safe_via_other_method")
        ):
            verdict = "safe_budget_tight"

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
            "remaining_after": str(rec.remaining_spendable)
            if rec and rec.remaining_spendable is not None
            else None,
        },
        "options": [
            {
                "method": o.method,
                "account_id": o.account_id,
                "account_name": o.account_name,
                "safe": o.safe,
                "reason": o.reason,
                "remaining_after": str(o.remaining_spendable)
                if o.remaining_spendable is not None
                else None,
                "card_plan_summary": o.card_plan_summary,
            }
            for o in options
        ],
        "ifpp_snapshot": {
            "cash_spendable": str(cash_raw),
            "budget_reserve": str(reserve.quantize(Decimal("0.01"))),
            "safe_to_spend": str(safe_to_spend.quantize(Decimal("0.01"))),
            "card_float_interest_free": str(ifpp.card_float_interest_free),
            "next_red_day": ifpp.next_red_day.isoformat() if ifpp.next_red_day else None,
        },
        "budget_check": budget_check,
        "principles": [
            "Never go negative on checking",
            "Safe to spend subtracts remaining period budgets (reserve)",
            "Intentional 0% float OK when full interest-free payoff path exists",
            "Do not charge if it forces revolving APR",
        ],
    }
