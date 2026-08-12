"""Pre-purchase check: can I buy this without going neg or paying interest?"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Category
from honestspend.engine.ifpp import CardView
from honestspend.services.ifpp_service import run_ifpp
from honestspend.services.payoff import plan_card_payoff

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
    from honestspend.services.budget_service import budgets_status

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
    allow_envelope_raid: bool = False,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    amount = abs(_d(amount))
    ifpp = run_ifpp(session, as_of=as_of, profile_id=profile_id, scope=scope)
    options: list[PurchaseOption] = []

    # Align with Home: Safe to spend = cash_spendable − period budget reserve
    from honestspend.services.budget_service import budget_reserve_total

    reserve = budget_reserve_total(
        session, profile_id=profile_id, as_of=as_of, scope=scope
    )
    cash_raw = _d(ifpp.cash_spendable)
    safe_to_spend = max(ZERO, cash_raw - reserve)
    # Envelope raid: liquidity before budgets covers amount, but Safe to spend does not
    envelope_raid: dict[str, Any] | None = None
    if cash_raw >= amount and safe_to_spend < amount and reserve > ZERO:
        raid_amt = (amount - safe_to_spend).quantize(Decimal("0.01"))
        envelope_raid = {
            "available": True,
            "raid_amount": str(raid_amt),
            "safe_to_spend": str(safe_to_spend.quantize(Decimal("0.01"))),
            "cash_before_budgets": str(cash_raw.quantize(Decimal("0.01"))),
            "budget_reserve": str(reserve.quantize(Decimal("0.01"))),
            "message": (
                f"Liquidity covers ${amount} if you raid ${raid_amt} from budget envelopes "
                f"(Safe to spend is only ${safe_to_spend})."
            ),
        }
    # When user explicitly allows raid, treat cash path against cash_raw for this check
    effective_safe = cash_raw if allow_envelope_raid and cash_raw >= amount else safe_to_spend

    # Per-account cash options (balance − account buffer)
    sc = (ifpp.details or {}).get("ifpp_scope") or scope or "entity"
    pid = (ifpp.details or {}).get("profile_id") or profile_id
    cq_cash = session.query(Account).filter(
        Account.archived_at.is_(None),
        Account.kind.in_(("checking", "savings", "cash")),
    )
    if sc == "entity" and pid is not None:
        cq_cash = cq_cash.filter(Account.profile_id == pid)
    cash_accounts = cq_cash.order_by(Account.id).all()
    best_cash: PurchaseOption | None = None
    for a in cash_accounts:
        bal = _d(a.current_balance)
        abuf = _d(getattr(a, "safety_buffer", None) or 0)
        avail = max(ZERO, bal - abuf)
        # Cap cash by Safe to spend (or cash_raw when user allows envelope raid)
        avail_eff = min(avail, effective_safe)
        ok = avail_eff >= amount and effective_safe >= amount
        reason = (
            f"{a.nickname}: ${avail_eff} available after account buffer "
            f"(capped by {'cash after bills/buffer' if allow_envelope_raid else 'Safe to spend'} ${effective_safe})."
            if ok
            else f"{a.nickname}: only ${avail_eff} after buffers / Safe to spend (need ${amount})."
        )
        opt = PurchaseOption(
            method="cash",
            account_id=a.id,
            account_name=a.nickname,
            safe=ok,
            reason=reason,
            remaining_spendable=(avail_eff - amount) if ok else avail_eff,
        )
        options.append(opt)
        if ok:
            if best_cash is None:
                best_cash = opt
            else:
                prev = next((x for x in cash_accounts if x.id == best_cash.account_id), None)
                # Prefer IFPP primary; else higher remaining
                if a.is_cash_for_ifpp and (prev is None or not prev.is_cash_for_ifpp):
                    best_cash = opt
                elif (prev is None or prev.is_cash_for_ifpp == a.is_cash_for_ifpp) and _d(
                    opt.remaining_spendable
                ) > _d(best_cash.remaining_spendable or 0):
                    best_cash = opt

    # Aggregate cash path (for prefer=cash without picking account)
    cash_ok = effective_safe >= amount
    if cash_ok and allow_envelope_raid and safe_to_spend < amount:
        cash_reason = (
            f"Envelope raid: cash after bills/buffer ${cash_raw} covers ${amount}; "
            f"raids ~${(amount - safe_to_spend).quantize(Decimal('0.01'))} from budgets "
            f"(normal Safe to spend is ${safe_to_spend})."
        )
    elif cash_ok:
        cash_reason = f"Safe to spend ${safe_to_spend} covers this purchase."
    else:
        cash_reason = (
            f"Safe to spend ${safe_to_spend} is short — would risk never-neg "
            f"or raid reserved budgets (${cash_raw} after buffers, ${reserve} budget reserve)."
        )
    options.insert(
        0,
        PurchaseOption(
            method="cash",
            account_id=None,
            account_name="Any cash / Safe to spend" + (" (envelope raid)" if allow_envelope_raid else ""),
            safe=cash_ok,
            reason=cash_reason,
            remaining_spendable=(effective_safe - amount) if cash_ok else effective_safe,
        ),
    )

    # Category label for rewards matching
    cat_hint = ""
    if category_id:
        from honestspend.db import Category

        cat = session.get(Category, category_id)
        if cat:
            cat_hint = f"{cat.display_name or ''} {cat.budget_group or ''}".lower()

    # Card paths (entity-scoped when IFPP is entity)
    from honestspend.services.promo_installments import effective_promo_balance

    cq = session.query(Account).filter(Account.kind == "credit", Account.archived_at.is_(None))
    if sc == "entity" and pid is not None:
        cq = cq.filter(Account.profile_id == pid)
    accounts = cq.all()
    card_views: list[CardView] = []
    rewards_by_id: dict[int, int] = {}
    for a in accounts:
        if account_id and a.id != account_id:
            continue
        score = 1 if a.rewards_program else 0
        prog = (a.rewards_program or "").lower()
        if cat_hint and prog:
            # Boost when rewards program text overlaps category
            for token in cat_hint.split():
                if len(token) > 3 and token in prog:
                    score += 3
                    break
            if any(k in prog for k in ("grocery", "gas", "travel", "dining", "food", "restaurant")):
                if any(k in cat_hint for k in ("food", "gas", "transport", "travel", "dining", "restaurant")):
                    score += 2
        rewards_by_id[a.id] = score
        promo_bal = effective_promo_balance(session, a, as_of=as_of)
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
            promo_balance=promo_bal,
            min_payment=Decimal(a.min_payment) if a.min_payment is not None else None,
            rewards_score=score,
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
            else "No interest-free path set up for this card yet — add due day / promo under Cards."
        )
        if cv.rewards_score > 1:
            reason += f" Rewards match score {cv.rewards_score}."
        if not safe:
            reason = (
                f"Not safe: only ${safe_amt} interest-free capacity. "
                f"Charging ${amount} risks revolving/APR. {plan.payoff_path if plan else ''}"
            )
        # Card payoff still needs cash after bills/buffer; envelopes optional with raid
        cash_for_card = cash_raw if allow_envelope_raid else safe_to_spend
        if safe and cash_for_card < amount:
            safe = False
            reason = (
                f"Interest-free capacity ${safe_amt}, but Safe to spend ${safe_to_spend} "
                f"is short of ${amount} (would raid envelopes or buffer)."
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

    # Recommendation — auto: cash first when both safe (envelope-honest)
    rec = None
    if prefer == "cash":
        rec = best_cash if best_cash and best_cash.safe else next(
            (o for o in options if o.method == "cash" and o.account_id is None), None
        )
    elif prefer == "card" and account_id:
        rec = next((o for o in options if o.account_id == account_id), None)
    elif prefer == "card":
        safe_cards = [o for o in options if o.method == "card" and o.safe]
        safe_cards.sort(key=lambda o: rewards_by_id.get(o.account_id or 0, 0), reverse=True)
        rec = safe_cards[0] if safe_cards else next((o for o in options if o.method == "card"), None)
    else:
        if best_cash and best_cash.safe:
            rec = best_cash
        else:
            any_cash = next(
                (o for o in options if o.method == "cash" and o.account_id is None and o.safe),
                None,
            )
            if any_cash:
                rec = any_cash
            else:
                safe_cards = [o for o in options if o.method == "card" and o.safe]
                safe_cards.sort(
                    key=lambda o: rewards_by_id.get(o.account_id or 0, 0), reverse=True
                )
                rec = safe_cards[0] if safe_cards else next(
                    (o for o in options if o.method == "cash" and o.account_id is None), None
                )

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
        if (
            budget_check.get("has_rule")
            and not budget_check.get("ok")
            and verdict in ("safe", "safe_via_other_method")
        ):
            verdict = "safe_budget_tight"
            if rec and rec.safe:
                rec.reason = (
                    (rec.reason or "")
                    + " · Category budget is short — stay in budget or raid envelope."
                ).strip()

    if allow_envelope_raid and rec and rec.safe and safe_to_spend < amount:
        verdict = "safe_raid_envelope"

    return {
        "amount": str(amount),
        "as_of": as_of.isoformat(),
        "verdict": verdict,
        "prefer_applied": prefer,
        "allow_envelope_raid": allow_envelope_raid,
        "safe_to_spend": str(safe_to_spend.quantize(Decimal("0.01"))),
        "budget_reserve": str(reserve.quantize(Decimal("0.01"))),
        "envelope_raid": envelope_raid,
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
            "Safe to spend subtracts remaining discretionary budgets (reserve)",
            "Auto prefers cash when Safe to spend covers",
            "Explicit envelope raid only when you choose it",
        ],
    }
