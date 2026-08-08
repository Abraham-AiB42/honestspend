"""Load debts/yields from accounts + run strategy / credit sim."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from financial_os.db import Account, AppSettings
from financial_os.services.credit_sim import (
    CreditProfileInputs,
    sim_to_dict,
    simulate_credit,
)
from financial_os.services.debt_strategy import (
    DebtView,
    YieldAccountView,
    compare_strategies,
    plan_to_dict,
    resolve_opportunity_rate,
    simulate_payoff,
)


def accounts_to_debts(session: Session) -> list[DebtView]:
    rows = (
        session.query(Account)
        .filter(Account.kind.in_(("credit", "loan")))
        .order_by(Account.id)
        .all()
    )
    debts: list[DebtView] = []
    for a in rows:
        bal = Decimal(a.current_balance or 0)
        if bal <= 0 and a.kind == "loan":
            continue
        debts.append(
            DebtView(
                id=a.id,
                name=a.nickname,
                kind=a.kind,
                balance=max(Decimal("0"), bal),
                apr=Decimal(a.apr or 0),
                min_payment=Decimal(a.min_payment or 0),
                credit_limit=Decimal(a.credit_limit) if a.credit_limit is not None else None,
                promo_apr=Decimal(a.promo_apr) if a.promo_apr is not None else None,
                promo_end_date=a.promo_end_date,
                promo_balance=Decimal(a.promo_balance) if a.promo_balance is not None else None,
                payment_due_day=a.payment_due_day,
                priority_rank=int(getattr(a, "priority_rank", None) or 100),
                opened_date=getattr(a, "opened_date", None),
            )
        )
    return debts


def accounts_to_yields(session: Session) -> list[YieldAccountView]:
    rows = session.query(Account).filter(
        Account.kind.in_(("checking", "savings", "cash", "other"))
    ).all()
    out: list[YieldAccountView] = []
    for a in rows:
        apy = getattr(a, "apy", None)
        if apy is None or Decimal(apy) <= 0:
            continue
        out.append(
            YieldAccountView(
                id=a.id,
                name=a.nickname,
                balance=Decimal(a.current_balance or 0),
                apy=Decimal(apy),
            )
        )
    return out


def opportunity_context(session: Session) -> tuple[Decimal | None, str, bool]:
    s = session.get(AppSettings, 1) or AppSettings(id=1)
    aware = bool(getattr(s, "opportunity_cost_aware", True))
    manual = getattr(s, "opportunity_rate", None)
    tax = getattr(s, "opportunity_tax_rate", None)
    yields = accounts_to_yields(session)
    rate, src = resolve_opportunity_rate(
        yields,
        manual_rate=Decimal(manual) if manual is not None else None,
        tax_rate=Decimal(tax) if tax is not None else None,
    )
    if rate <= 0 and not yields and manual is None:
        return None, src, aware
    return rate, src, aware


def get_credit_profile(session: Session) -> CreditProfileInputs:
    s = session.get(AppSettings, 1) or AppSettings(id=1)
    return CreditProfileInputs(
        on_time_rate=Decimal(str(getattr(s, "credit_on_time_rate", None) or "1")),
        late_30_12m=int(getattr(s, "credit_late_30", None) or 0),
        late_60_12m=int(getattr(s, "credit_late_60", None) or 0),
        late_90_plus_12m=int(getattr(s, "credit_late_90", None) or 0),
        hard_inquiries_12m=int(getattr(s, "credit_hard_inquiries", None) or 0),
        new_accounts_12m=int(getattr(s, "credit_new_accounts", None) or 0),
        reported_vantage=getattr(s, "credit_reported_vantage", None),
    )


def run_debt_plan(
    session: Session,
    *,
    strategy: str | None = None,
    extra_monthly: Decimal = Decimal("0"),
    opportunity_cost_aware: bool | None = None,
) -> dict:
    s = session.get(AppSettings, 1) or AppSettings(id=1)
    strat = (strategy or getattr(s, "debt_strategy", None) or "avalanche").lower()
    if strat not in ("avalanche", "snowball", "promo_guard", "utilization", "custom"):
        strat = "avalanche"
    opp_rate, opp_src, aware_default = opportunity_context(session)
    aware = aware_default if opportunity_cost_aware is None else opportunity_cost_aware
    debts = accounts_to_debts(session)
    plan = simulate_payoff(
        debts,
        strategy=strat,  # type: ignore[arg-type]
        extra_monthly=extra_monthly,
        opportunity_rate=opp_rate,
        opportunity_rate_source=opp_src,
        opportunity_cost_aware=aware,
    )
    out = plan_to_dict(plan)
    out["yield_accounts"] = [
        {
            "id": y.id,
            "name": y.name,
            "balance": str(y.balance),
            "apy_pct": f"{float(y.apy)*100:.3f}%",
        }
        for y in accounts_to_yields(session)
    ]
    return out


def run_strategy_compare(session: Session, *, extra_monthly: Decimal = Decimal("0")) -> dict:
    debts = accounts_to_debts(session)
    opp_rate, opp_src, aware = opportunity_context(session)
    return {
        "comparisons": compare_strategies(
            debts,
            extra_monthly=extra_monthly,
            opportunity_rate=opp_rate,
            opportunity_rate_source=opp_src,
            opportunity_cost_aware=aware,
        ),
        "opportunity_rate_source": opp_src,
        "opportunity_rate": str(opp_rate) if opp_rate is not None else None,
        "disclaimer": (
            "Interest estimates use APR/promo on each debt. "
            "Opportunity cost uses max cash APY or your manual hurdle "
            "(e.g. do not prepay 2.625% mortgage when cash pays 6%)."
        ),
    }


def run_credit_health(session: Session) -> dict:
    debts = accounts_to_debts(session)
    profile = get_credit_profile(session)
    result = simulate_credit(debts, profile)
    data = sim_to_dict(result)
    if profile.reported_vantage:
        data["your_reported_vantage"] = profile.reported_vantage
        data["vs_reported_delta"] = result.score - profile.reported_vantage
    return data
