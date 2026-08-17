"""Rank charge accounts: safety, then longest float, then rewards.

The product's only charge-account opinion. Later surfaces (Can I buy?, rewards
pick, Home whisper, offers) call rank_charge rather than re-ranking.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Category
from honestspend.engine.ifpp import CardView, card_safe_to_charge
from honestspend.services.budget_service import budget_reserve_total, budgets_status
from honestspend.services.ifpp_service import run_ifpp
from honestspend.services.promo_installments import effective_promo_balance
from honestspend.services.rewards_pick import rate_for_category
from honestspend.services.statement_cycle import (
    next_payment_date,
    normalize_timing,
    project_card_payment,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")
CASH_KINDS = frozenset({"checking", "savings", "cash"})
DUE_DAY_REASON = "Add due day and pay-from on this card."


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return _d(v).quantize(CENT)


@dataclass
class ChargeOption:
    method: str  # cash | card | bnpl
    account_id: int | None
    account_name: str
    safe: bool
    reason: str
    float_days: int
    rewards_rate: Decimal  # percent points
    next_payment_after: Decimal | None
    promo_used: str | None  # none | card_intro | purchase_plan | bnpl
    remaining_spendable: Decimal | None
    first_due: Decimal | None = None
    installment: Decimal | None = None
    finance_charge: Decimal | None = None
    total_cost: Decimal | None = None
    extra_cost: Decimal | None = None
    payments: int | None = None
    cadence: str | None = None
    late_fee_typical: Decimal | None = None
    bnpl_provider: str | None = None


def _promo_mode(promo: dict | None) -> str:
    if not promo:
        return "none"
    return str(promo.get("mode") or "none").strip().lower() or "none"


def _purchase_plan_monthly(promo: dict | None, amount: Decimal) -> Decimal:
    if _promo_mode(promo) != "purchase_plan" or not promo:
        return ZERO
    raw = promo.get("monthly")
    if raw not in (None, ""):
        return _q(_d(raw))
    months = promo.get("months")
    if months in (None, ""):
        return ZERO
    n = int(months)
    if n <= 0:
        return ZERO
    return (amount / Decimal(n)).quantize(CENT, rounding=ROUND_HALF_UP)


def _has_proven_pay_date(acct: Account) -> bool:
    due = acct.payment_due_day
    if due is not None and int(due) >= 1:
        return True
    timing = normalize_timing(getattr(acct, "payment_timing", None))
    close = acct.statement_close_day
    if close is not None and int(close) >= 1 and timing in ("on_close", "day_before_close"):
        return True
    return False


def _card_float_days(acct: Account, as_of: date) -> int:
    pay_date = next_payment_date(
        as_of=as_of,
        timing=getattr(acct, "payment_timing", None),
        close_day=acct.statement_close_day,
        due_day=acct.payment_due_day,
    )
    if pay_date is None:
        return 0
    return max(0, (pay_date - as_of).days)


def _utilization(acct: Account, extra_balance: Decimal) -> Decimal:
    limit = _d(acct.credit_limit)
    if limit <= ZERO:
        return Decimal("999")
    bal = _d(acct.current_balance) + extra_balance
    return (bal / limit * Decimal("100")).quantize(Decimal("0.01"))


def _promo_used_for_card(acct: Account, *, promo: dict | None, as_of: date) -> str:
    mode = _promo_mode(promo)
    if mode == "bnpl":
        return "bnpl"
    if mode == "purchase_plan":
        return "purchase_plan"
    if mode == "card_intro":
        end = getattr(acct, "promo_end_date", None)
        apr = getattr(acct, "promo_apr", None)
        if end is not None and end >= as_of and (apr is None or _d(apr) == ZERO):
            return "card_intro"
        return "none"
    return "none"


def _budget_check(
    session: Session,
    *,
    profile_id: int | None,
    category_id: int,
    amount: Decimal,
    as_of: date,
) -> dict[str, Any]:
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


def _card_view(session: Session, acct: Account, as_of: date) -> CardView:
    promo_bal = effective_promo_balance(session, acct, as_of=as_of)
    return CardView(
        id=acct.id,
        name=acct.nickname,
        balance=_d(acct.current_balance),
        credit_limit=_d(acct.credit_limit),
        available_credit=_d(acct.available_credit),
        statement_close_day=acct.statement_close_day,
        payment_due_day=acct.payment_due_day,
        apr=_d(acct.apr) if acct.apr is not None else None,
        promo_apr=_d(acct.promo_apr) if acct.promo_apr is not None else None,
        promo_end_date=acct.promo_end_date,
        promo_balance=promo_bal,
        min_payment=_d(acct.min_payment) if acct.min_payment is not None else None,
        rewards_score=1 if acct.rewards_program else 0,
    )


def _safe_to_charge_for(
    session: Session,
    acct: Account,
    *,
    as_of: date,
    card_plans: dict[int, Any],
    cash_for_payoff: Decimal,
) -> Decimal:
    plan = card_plans.get(acct.id)
    if plan is not None:
        return _d(plan.safe_to_charge)
    built = card_safe_to_charge(
        _card_view(session, acct, as_of),
        as_of=as_of,
        cash_available_for_payoff=cash_for_payoff,
        schedule=[],
        mode="expected",
    )
    return _d(built.safe_to_charge)


def _cash_option(
    acct: Account,
    *,
    amount: Decimal,
    effective_safe: Decimal,
) -> ChargeOption:
    bal = _d(acct.current_balance)
    abuf = _d(getattr(acct, "safety_buffer", None) or 0)
    avail = max(ZERO, bal - abuf)
    avail_eff = min(avail, effective_safe)
    ok = avail_eff >= amount
    if ok:
        reason = (
            f"{acct.nickname}: ${avail_eff} available after account buffer "
            f"(capped by Safe to spend ${effective_safe})."
        )
        remaining = avail_eff - amount
    else:
        reason = (
            f"{acct.nickname}: only ${avail_eff} after buffers / Safe to spend "
            f"(need ${amount})."
        )
        remaining = avail_eff
    return ChargeOption(
        method="cash",
        account_id=acct.id,
        account_name=acct.nickname,
        safe=ok,
        reason=reason,
        float_days=0,
        rewards_rate=ZERO,
        next_payment_after=None,
        promo_used="none",
        remaining_spendable=remaining,
    )


def _card_option(
    session: Session,
    acct: Account,
    *,
    amount: Decimal,
    reward_category: str,
    as_of: date,
    safe_to_spend: Decimal,
    card_plans: dict[int, Any],
    cash_for_payoff: Decimal,
    promo: dict | None,
) -> tuple[ChargeOption, Decimal, Decimal]:
    rate = _d(rate_for_category(acct, reward_category))
    promo_used = _promo_used_for_card(acct, promo=promo, as_of=as_of)
    extra_monthly = _purchase_plan_monthly(promo, amount)

    if not _has_proven_pay_date(acct):
        opt = ChargeOption(
            method="card",
            account_id=acct.id,
            account_name=acct.nickname,
            safe=False,
            reason=DUE_DAY_REASON,
            float_days=0,
            rewards_rate=rate,
            next_payment_after=None,
            promo_used=promo_used,
            remaining_spendable=None,
        )
        return opt, Decimal("999"), ZERO

    float_days = _card_float_days(acct, as_of)
    safe_amt = _safe_to_charge_for(
        session,
        acct,
        as_of=as_of,
        card_plans=card_plans,
        cash_for_payoff=cash_for_payoff,
    )
    before = project_card_payment(session, acct.id, as_of=as_of)
    after = project_card_payment(
        session,
        acct.id,
        as_of=as_of,
        extra_balance=amount,
        extra_monthly=extra_monthly if extra_monthly > ZERO else None,
    )
    next_before = _d(before.get("next_payment"))
    next_after = _d(after.get("next_payment"))
    increase = max(ZERO, next_after - next_before)
    util = _utilization(acct, amount)

    parts: list[str] = []
    safe = True
    if safe_amt < amount:
        safe = False
        parts.append(
            f"Interest-free capacity ${safe_amt} is short of ${amount}."
        )
    if next_after > safe_to_spend:
        safe = False
        parts.append(
            f"Next payment would be ${next_after}, more than Safe to spend "
            f"${safe_to_spend}."
        )
    if safe:
        reason = (
            f"{acct.nickname}: {float_days} interest-free days, "
            f"{rate}% {reward_category}."
        )
        remaining = safe_amt - amount
    else:
        reason = " ".join(parts) if parts else "Not safe to charge."
        remaining = safe_amt

    opt = ChargeOption(
        method="card",
        account_id=acct.id,
        account_name=acct.nickname,
        safe=safe,
        reason=reason,
        float_days=float_days,
        rewards_rate=rate,
        next_payment_after=next_after,
        promo_used=promo_used,
        remaining_spendable=remaining,
    )
    return opt, util, increase


def _bnpl_option(
    acct: Account,
    *,
    quote: dict[str, Any],
    effective_safe: Decimal,
    existing_remaining: Decimal,
    card_safe: Decimal | None,
) -> ChargeOption:
    first = _d(quote.get("first_due"))
    installment = _d(quote.get("installment"))
    remaining = _d(quote.get("remaining_total"))
    fees = _d(quote.get("fees"))
    extra = _d(quote.get("extra_cost"))
    late = _d(quote.get("late_fee_typical"))
    n = int(quote.get("payments") or 4)
    cadence = str(quote.get("cadence") or "biweekly")
    name = str(quote.get("provider_name") or "BNPL")
    kind = (acct.kind or "").lower()
    if kind in CASH_KINDS or acct.is_cash_for_ifpp:
        bal = _d(acct.current_balance)
        abuf = _d(getattr(acct, "safety_buffer", None) or 0)
        avail = max(ZERO, bal - abuf)
        cap = min(avail, effective_safe)
        ok = first <= cap
        leftover = cap - first if ok else cap
    else:
        cap = card_safe if card_safe is not None else ZERO
        ok = first <= cap
        leftover = cap - first if ok else cap
    tight = ok and leftover <= remaining
    parts = [
        f"{name}: ${first} due today, then {n - 1}×${installment} {cadence}.",
        f"Total ${quote.get('total_cost')} (fees ${fees}"
        + (f", ${extra} more than paying now" if extra > ZERO else ", no extra if on time")
        + ").",
    ]
    if late > ZERO:
        parts.append(f"Typical late fee ${late} if you miss (not included).")
    if existing_remaining > ZERO:
        parts.append(f"You already have ${existing_remaining} in BNPL still coming due.")
    if not ok:
        parts.insert(0, f"First payment ${first} does not fit (need ${first}, have ${cap}).")
        reason = " ".join(parts)
        remaining_spendable = cap
    elif tight:
        parts.append(
            f"Tight: after ${first} today, ${remaining} more is due on this plan "
            f"and only ${leftover} Safe is left."
        )
        reason = " ".join(parts)
        remaining_spendable = leftover
    else:
        reason = " ".join(parts)
        remaining_spendable = leftover
    return ChargeOption(
        method="bnpl",
        account_id=acct.id,
        account_name=f"{name} · {acct.nickname}",
        safe=ok,
        reason=reason,
        float_days=0,
        rewards_rate=ZERO,
        next_payment_after=first,
        promo_used="bnpl",
        remaining_spendable=remaining_spendable,
        first_due=first,
        installment=installment,
        finance_charge=_d(quote.get("finance_charge")),
        total_cost=_d(quote.get("total_cost")),
        extra_cost=extra,
        payments=n,
        cadence=cadence,
        late_fee_typical=late if late > ZERO else None,
        bnpl_provider=str(quote.get("provider") or ""),
    )


def rank_charge(
    session: Session,
    *,
    amount: Decimal,
    reward_category: str,
    proposed_account_id: int,
    as_of: date | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
    promo: dict | None = None,
    budget_category_id: int | None = None,
    allow_envelope_raid: bool = False,
) -> dict:
    """promo = {mode: none|card_intro|purchase_plan|bnpl, months?: int, monthly?: str,
    provider?, plan?, payments?, apr?, origination_fee?, service_fee?}
    Returns {proposed, recommended, options, verdict, why, budget_check, envelope_raid,
    safe_to_spend, bnpl_quote, existing_bnpl}.
    """
    as_of = as_of or date.today()
    amount = abs(_d(amount))
    ifpp = run_ifpp(session, as_of=as_of, profile_id=profile_id, scope=scope)
    reserve = budget_reserve_total(
        session, profile_id=profile_id, as_of=as_of, scope=scope
    )
    cash_raw = _d(ifpp.cash_spendable)
    safe_to_spend = max(ZERO, cash_raw - reserve)

    envelope_raid: dict[str, Any] | None = None
    if cash_raw >= amount and safe_to_spend < amount and reserve > ZERO:
        raid_amt = (amount - safe_to_spend).quantize(CENT)
        envelope_raid = {
            "available": True,
            "raid_amount": str(raid_amt),
            "safe_to_spend": str(_q(safe_to_spend)),
            "cash_before_budgets": str(_q(cash_raw)),
            "budget_reserve": str(_q(reserve)),
            "message": (
                f"Liquidity covers ${amount} if you raid ${raid_amt} from budget envelopes "
                f"(Safe to spend is only ${safe_to_spend})."
            ),
        }
    effective_safe = (
        cash_raw if allow_envelope_raid and cash_raw >= amount else safe_to_spend
    )

    sc = (ifpp.details or {}).get("ifpp_scope") or scope or "entity"
    pid = (ifpp.details or {}).get("profile_id") or profile_id
    q = session.query(Account).filter(Account.archived_at.is_(None))
    if sc == "entity" and pid is not None:
        q = q.filter(Account.profile_id == pid)
    accounts = list(q.order_by(Account.id).all())
    proposed_acct = session.get(Account, proposed_account_id)
    if proposed_acct is not None and all(a.id != proposed_acct.id for a in accounts):
        accounts.append(proposed_acct)

    card_plans = {c.account_id: c for c in (ifpp.cards or [])}
    cash_for_payoff = cash_raw

    options: list[ChargeOption] = []
    util_by_id: dict[int | None, Decimal] = {}
    increase_by_id: dict[int | None, Decimal] = {}

    for acct in accounts:
        kind = (acct.kind or "").lower()
        if kind in CASH_KINDS or acct.is_cash_for_ifpp:
            opt = _cash_option(acct, amount=amount, effective_safe=effective_safe)
            options.append(opt)
            util_by_id[acct.id] = ZERO
            increase_by_id[acct.id] = ZERO
        elif kind == "credit":
            opt, util, increase = _card_option(
                session,
                acct,
                amount=amount,
                reward_category=reward_category,
                as_of=as_of,
                safe_to_spend=safe_to_spend,
                card_plans=card_plans,
                cash_for_payoff=cash_for_payoff,
                promo=promo,
            )
            options.append(opt)
            util_by_id[acct.id] = util
            increase_by_id[acct.id] = increase

    from honestspend.services.bnpl import existing_bnpl_burden, quote_bnpl

    existing_bnpl = existing_bnpl_burden(
        session, profile_id=pid if pid is not None else profile_id, as_of=as_of
    )
    existing_rem = _d(existing_bnpl.get("remaining_total"))
    bnpl_quote: dict[str, Any] | None = None
    if _promo_mode(promo) == "bnpl" and proposed_acct is not None:
        try:
            bnpl_quote = quote_bnpl(
                amount,
                provider=(promo or {}).get("provider"),
                plan=(promo or {}).get("plan"),
                payments=(promo or {}).get("payments"),
                apr=(promo or {}).get("apr"),
                origination_fee=(promo or {}).get("origination_fee"),
                service_fee=(promo or {}).get("service_fee"),
                as_of=as_of,
            )
        except ValueError:
            bnpl_quote = None
        if bnpl_quote:
            first = _d(bnpl_quote.get("first_due"))
            if cash_raw >= first and safe_to_spend < first and reserve > ZERO:
                raid_amt = (first - safe_to_spend).quantize(CENT)
                envelope_raid = {
                    "available": True,
                    "raid_amount": str(raid_amt),
                    "safe_to_spend": str(_q(safe_to_spend)),
                    "cash_before_budgets": str(_q(cash_raw)),
                    "budget_reserve": str(_q(reserve)),
                    "message": (
                        f"First BNPL payment ${first} needs a ${raid_amt} envelope raid "
                        f"(Safe to spend is only ${safe_to_spend})."
                    ),
                }
            bnpl_safe_cap = (
                cash_raw if allow_envelope_raid and cash_raw >= first else safe_to_spend
            )
            card_safe = None
            if (proposed_acct.kind or "").lower() == "credit":
                card_safe = _safe_to_charge_for(
                    session,
                    proposed_acct,
                    as_of=as_of,
                    card_plans=card_plans,
                    cash_for_payoff=cash_for_payoff,
                )
            bnpl_opt = _bnpl_option(
                proposed_acct,
                quote=bnpl_quote,
                effective_safe=bnpl_safe_cap,
                existing_remaining=existing_rem,
                card_safe=card_safe,
            )
            options.append(bnpl_opt)
            util_by_id[proposed_acct.id] = util_by_id.get(proposed_acct.id, ZERO)
            increase_by_id[proposed_acct.id] = first

    def _rank_key(opt: ChargeOption) -> tuple:
        proposed_first = 0 if opt.account_id == proposed_account_id else 1
        return (
            0 if opt.method != "bnpl" else 1,
            -int(opt.float_days),
            -_d(opt.rewards_rate),
            proposed_first,
            util_by_id.get(opt.account_id, ZERO),
            increase_by_id.get(opt.account_id, ZERO),
        )

    safe_opts = [o for o in options if o.safe]
    unsafe_opts = [o for o in options if not o.safe]
    safe_opts.sort(key=_rank_key)
    unsafe_opts.sort(key=_rank_key)
    ordered = safe_opts + unsafe_opts

    proposed_opt = next(
        (o for o in ordered if o.account_id == proposed_account_id), None
    )
    rec = safe_opts[0] if safe_opts else proposed_opt
    if _promo_mode(promo) == "bnpl":
        bnpl_safe = next((o for o in safe_opts if o.method == "bnpl"), None)
        if bnpl_safe is not None:
            rec = bnpl_safe

    if rec and rec.safe:
        if rec.account_id == proposed_account_id:
            verdict = "safe"
        else:
            verdict = "safe_via_other_account"
    else:
        verdict = "do_not_buy"

    budget_check: dict[str, Any] | None = None
    if budget_category_id:
        budget_check = _budget_check(
            session,
            profile_id=profile_id,
            category_id=int(budget_category_id),
            amount=amount,
            as_of=as_of,
        )
        if (
            budget_check.get("has_rule")
            and not budget_check.get("ok")
            and verdict in ("safe", "safe_via_other_account")
        ):
            verdict = "safe_budget_tight"

    raid_need = (
        _d(bnpl_quote.get("first_due"))
        if bnpl_quote is not None
        else amount
    )
    if allow_envelope_raid and rec and rec.safe and safe_to_spend < raid_need:
        verdict = "safe_raid_envelope"

    if rec and rec.safe:
        if rec.method == "bnpl":
            why = rec.reason
        elif rec.method == "card":
            why = (
                f"Charge {rec.account_name} — {rec.float_days} interest-free days, "
                f"{rec.rewards_rate}% {reward_category}."
            )
        else:
            why = (
                f"Use {rec.account_name} — no card is safe for this purchase."
            )
    else:
        why = "Do not buy — no account is safe for this amount."
    if existing_rem > ZERO and rec and rec.method != "bnpl":
        why += f" You already have ${existing_rem} in BNPL still coming due."

    return {
        "proposed": asdict(proposed_opt) if proposed_opt else None,
        "recommended": asdict(rec) if rec else None,
        "options": [asdict(o) for o in ordered],
        "verdict": verdict,
        "why": why,
        "budget_check": budget_check,
        "envelope_raid": envelope_raid,
        "safe_to_spend": str(_q(safe_to_spend)),
        "bnpl_quote": bnpl_quote,
        "existing_bnpl": existing_bnpl,
    }
