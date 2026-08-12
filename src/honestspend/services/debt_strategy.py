"""Debt payoff prioritization — selectable strategies + opportunity cost.

If cash yields 6% (e.g. high-yield savings) and a mortgage costs 2.625%, extra principal
on the mortgage is usually worse than keeping money in the yield account.

Strategies (among debts that beat the hurdle when opportunity-aware):
  avalanche   — highest APR first (min interest total)  [default]
  snowball    — lowest balance first
  promo_guard — expiring 0% first, then avalanche
  utilization — highest util% first
  custom      — user priority_rank
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from honestspend.engine.schedule_expand import add_months

ZERO = Decimal("0")
Strategy = Literal["avalanche", "snowball", "promo_guard", "utilization", "custom"]


@dataclass
class DebtView:
    id: int
    name: str
    kind: str  # credit | loan
    balance: Decimal
    apr: Decimal  # e.g. 0.02625 mortgage, 0.2299 card
    min_payment: Decimal
    credit_limit: Decimal | None = None
    promo_apr: Decimal | None = None
    promo_end_date: date | None = None
    promo_balance: Decimal | None = None
    payment_due_day: int | None = None
    priority_rank: int = 100
    opened_date: date | None = None


@dataclass
class YieldAccountView:
    id: int
    name: str
    balance: Decimal
    apy: Decimal  # e.g. 0.06


@dataclass
class PayoffOrderItem:
    account_id: int
    name: str
    strategy_rank: int
    balance: Decimal
    effective_apr: Decimal
    min_payment: Decimal
    utilization_pct: Decimal | None
    reason: str
    monthly_interest_est: Decimal
    promo_days_left: int | None = None
    # Opportunity cost
    beats_opportunity: bool = True
    spread_vs_opportunity: Decimal | None = None  # debt APR - hurdle (positive = pay debt)
    recommendation: str = "extra_ok"  # extra_ok | minimum_only | promo_watch


@dataclass
class DebtPlanResult:
    strategy: str
    as_of: date
    extra_monthly: Decimal
    order: list[PayoffOrderItem]
    schedule_summary: list[dict[str, Any]] = field(default_factory=list)
    total_balance: Decimal = ZERO
    total_min_payments: Decimal = ZERO
    estimated_months: int | None = None
    estimated_interest: Decimal = ZERO
    notes: list[str] = field(default_factory=list)
    opportunity_rate: Decimal | None = None
    opportunity_rate_source: str | None = None
    opportunity_cost_aware: bool = True
    # Extra that should stay in yield accounts rather than cheap debt
    extra_to_yield_not_debt: Decimal = ZERO
    invest_vs_debt: list[dict[str, Any]] = field(default_factory=list)


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _promo_active(debt: DebtView, as_of: date) -> bool:
    if debt.promo_end_date is None or debt.promo_end_date < as_of:
        return False
    if debt.promo_apr is None:
        return False
    return _d(debt.promo_apr) == ZERO


def effective_apr(debt: DebtView, as_of: date) -> Decimal:
    """APR currently accruing (0% promo when active)."""
    if _promo_active(debt, as_of):
        promo_bal = _d(debt.promo_balance)
        bal = max(ZERO, _d(debt.balance))
        if promo_bal > ZERO and promo_bal < bal:
            non = bal - promo_bal
            return ((promo_bal * ZERO) + (non * _d(debt.apr))) / bal if bal else ZERO
        return ZERO
    return max(ZERO, _d(debt.apr))


def monthly_interest(balance: Decimal, apr: Decimal) -> Decimal:
    return (max(ZERO, balance) * max(ZERO, apr) / Decimal("12")).quantize(Decimal("0.01"))


def utilization(debt: DebtView) -> Decimal | None:
    lim = _d(debt.credit_limit)
    if lim <= ZERO or debt.kind != "credit":
        return None
    return (max(ZERO, _d(debt.balance)) / lim * Decimal("100")).quantize(Decimal("0.1"))


def resolve_opportunity_rate(
    yield_accounts: list[YieldAccountView],
    *,
    manual_rate: Decimal | None = None,
    tax_rate: Decimal | None = None,
) -> tuple[Decimal, str]:
    """Return (rate, source label). After-tax if tax_rate set: APY * (1 - tax)."""
    if manual_rate is not None and _d(manual_rate) > ZERO:
        rate = _d(manual_rate)
        src = f"manual hurdle {float(rate)*100:.3f}%"
    else:
        best = ZERO
        best_name = None
        for y in yield_accounts:
            apy = _d(y.apy)
            if apy > best:
                best = apy
                best_name = y.name
        if best_name:
            rate = best
            src = f"{best_name} APY {float(rate)*100:.3f}%"
        else:
            rate = ZERO
            src = "no yield accounts — set APY on savings/HYSA or a manual hurdle"

    if tax_rate is not None and _d(tax_rate) > ZERO:
        after = rate * (Decimal("1") - _d(tax_rate))
        src = f"{src} → after-tax ~{float(after)*100:.3f}% (tax {float(_d(tax_rate))*100:.0f}%)"
        rate = after
    return rate, src


def invest_vs_debt_rows(
    debts: list[DebtView],
    *,
    opportunity_rate: Decimal,
    as_of: date,
    opportunity_source: str,
) -> list[dict[str, Any]]:
    """Per-debt: is extra principal better than earning opportunity_rate?"""
    rows = []
    for d in debts:
        bal = max(ZERO, _d(d.balance))
        if bal <= ZERO:
            continue
        eapr = effective_apr(d, as_of)
        spread = eapr - opportunity_rate
        if eapr < opportunity_rate:
            rec = "minimum_only"
            verdict = (
                f"Keep cash earning ~{float(opportunity_rate)*100:.2f}% instead of "
                f"prepaying {float(eapr)*100:.3f}% debt. Spread "
                f"{float(spread)*100:.2f} pp against prepay."
            )
        elif eapr == opportunity_rate:
            rec = "indifferent"
            verdict = "Debt rate ≈ opportunity yield — indifference (prefer liquidity)."
        else:
            rec = "extra_ok"
            verdict = (
                f"Debt costs {float(eapr)*100:.2f}% > yield {float(opportunity_rate)*100:.2f}% — "
                f"extra payments save money."
            )
        # Example: $1000 left in yield vs applied to debt for 1 year (simple, not compound edge cases)
        on_yield = (Decimal("1000") * opportunity_rate).quantize(Decimal("0.01"))
        interest_saved = (Decimal("1000") * eapr).quantize(Decimal("0.01"))
        rows.append(
            {
                "account_id": d.id,
                "name": d.name,
                "kind": d.kind,
                "balance": str(bal),
                "effective_apr_pct": f"{float(eapr)*100:.3f}%",
                "opportunity_rate_pct": f"{float(opportunity_rate)*100:.3f}%",
                "opportunity_source": opportunity_source,
                "spread_pp": f"{float(spread)*100:.3f}",
                "recommendation": rec,
                "verdict": verdict,
                "example_per_1000": {
                    "earn_in_yield_1y": str(on_yield),
                    "interest_avoided_on_debt_1y": str(interest_saved),
                    "edge_of_keeping_cash": str((on_yield - interest_saved).quantize(Decimal("0.01"))),
                },
            }
        )
    rows.sort(key=lambda r: Decimal(r["spread_pp"]))
    return rows


def sort_debts(
    debts: list[DebtView],
    strategy: Strategy,
    as_of: date,
    *,
    opportunity_rate: Decimal | None = None,
    opportunity_cost_aware: bool = True,
) -> list[PayoffOrderItem]:
    hurdle = _d(opportunity_rate) if opportunity_cost_aware and opportunity_rate is not None else None
    items: list[tuple[Any, DebtView, str, bool, Decimal | None, str]] = []

    for d in debts:
        bal = max(ZERO, _d(d.balance))
        if bal <= ZERO:
            continue
        eapr = effective_apr(d, as_of)
        util = utilization(d)
        promo_days = None
        if d.promo_end_date and d.promo_end_date >= as_of:
            promo_days = (d.promo_end_date - as_of).days

        spread = (eapr - hurdle) if hurdle is not None else None
        beats = True if hurdle is None else eapr > hurdle
        # 0% promo still "beats" for extra only near end — extra during deep 0% is optional;
        # we allow extra on promo only for balloon timing, not as avalanche target early
        rec = "extra_ok"
        if hurdle is not None and not beats:
            rec = "minimum_only"

        if strategy == "avalanche":
            key = (-eapr, -bal, d.name)
            reason = f"Highest accruing APR ({float(eapr)*100:.2f}%) — minimize interest"
        elif strategy == "snowball":
            key = (bal, -eapr, d.name)
            reason = f"Lowest balance (${bal}) — fastest wins"
        elif strategy == "utilization":
            u = util if util is not None else Decimal("-1")
            key = (-u, -eapr, d.name)
            reason = (
                f"Highest utilization ({util}%) — score-friendly"
                if util is not None
                else "No limit set — APR fallback"
            )
        elif strategy == "promo_guard":
            urgency = promo_days if promo_days is not None else 10_000
            on_promo = 0 if (promo_days is not None and eapr == ZERO) else 1
            key = (on_promo, urgency, -eapr, d.name)
            if on_promo == 0:
                reason = f"0% ends in {promo_days}d — balloon before APR"
                rec = "promo_watch"
            else:
                reason = f"After promos: APR {float(eapr)*100:.2f}%"
        else:
            key = (d.priority_rank, -eapr, d.name)
            reason = f"Your priority rank {d.priority_rank}"

        if hurdle is not None and not beats and rec != "promo_watch":
            reason = (
                f"Min only: debt {float(eapr)*100:.3f}% < opportunity "
                f"{float(hurdle)*100:.3f}% — keep cash earning"
            )
            # Push below-hurdle to end of extra-payment queue
            key = (1, *key) if not isinstance(key[0], int) or key[0] != 1 else key
            # Use sort key that always trails "extra" targets
            key = (9_999, eapr, bal, d.name)

        items.append((key, d, reason, beats if hurdle is not None else True, spread, rec))

    items.sort(key=lambda x: x[0])
    out: list[PayoffOrderItem] = []
    for rank, (_, d, reason, beats, spread, rec) in enumerate(items, start=1):
        eapr = effective_apr(d, as_of)
        bal = max(ZERO, _d(d.balance))
        min_p = _d(d.min_payment)
        if min_p <= ZERO:
            min_p = max(Decimal("25"), (bal * Decimal("0.02")).quantize(Decimal("0.01")))
        promo_days = None
        if d.promo_end_date and d.promo_end_date >= as_of:
            promo_days = (d.promo_end_date - as_of).days
        out.append(
            PayoffOrderItem(
                account_id=d.id,
                name=d.name,
                strategy_rank=rank,
                balance=bal,
                effective_apr=eapr,
                min_payment=min_p,
                utilization_pct=utilization(d),
                reason=reason,
                monthly_interest_est=monthly_interest(bal, eapr),
                promo_days_left=promo_days,
                beats_opportunity=beats,
                spread_vs_opportunity=spread,
                recommendation=rec,
            )
        )
    return out


def simulate_payoff(
    debts: list[DebtView],
    *,
    strategy: Strategy = "avalanche",
    extra_monthly: Decimal = ZERO,
    as_of: date | None = None,
    max_months: int = 600,
    opportunity_rate: Decimal | None = None,
    opportunity_rate_source: str | None = None,
    opportunity_cost_aware: bool = True,
) -> DebtPlanResult:
    """Mins on all debts; extra only to targets that beat opportunity hurdle."""
    as_of = as_of or date.today()
    extra = max(ZERO, _d(extra_monthly))
    order = sort_debts(
        debts,
        strategy,
        as_of,
        opportunity_rate=opportunity_rate,
        opportunity_cost_aware=opportunity_cost_aware,
    )
    ivd = []
    if opportunity_cost_aware and opportunity_rate is not None:
        ivd = invest_vs_debt_rows(
            debts,
            opportunity_rate=_d(opportunity_rate),
            as_of=as_of,
            opportunity_source=opportunity_rate_source or "opportunity rate",
        )

    state: dict[int, dict[str, Any]] = {}
    for d in debts:
        bal = max(ZERO, _d(d.balance))
        if bal <= ZERO:
            continue
        min_p = _d(d.min_payment)
        if min_p <= ZERO:
            min_p = max(Decimal("25"), (bal * Decimal("0.02")).quantize(Decimal("0.01")))
        state[d.id] = {
            "name": d.name,
            "balance": bal,
            "apr": effective_apr(d, as_of),
            "min": min_p,
            "debt": d,
        }

    total_bal = sum((s["balance"] for s in state.values()), ZERO)
    total_min = sum((s["min"] for s in state.values()), ZERO)
    notes = [
        f"Strategy: {strategy}",
        "Always pay minimums on every debt.",
        "Extra payment goes only to the current focus target after minimums.",
    ]
    if opportunity_cost_aware and opportunity_rate is not None and _d(opportunity_rate) > ZERO:
        notes.append(
            f"Opportunity-cost aware: hurdle {_pct(opportunity_rate)} "
            f"({opportunity_rate_source or 'set rate'}). "
            f"Debts cheaper than this get minimum only — park extra in yield."
        )
        notes.append(
            "Example: mortgage 2.625% vs cash 6% → do not prepay mortgage; earn the spread."
        )
    elif opportunity_cost_aware:
        notes.append(
            "Opportunity-cost on, but no APY/hurdle set — add APY on savings/HYSA or a manual rate."
        )

    if strategy == "avalanche":
        notes.append("Among debts that beat the hurdle, avalanche attacks highest APR first.")
    elif strategy == "snowball":
        notes.append("Snowball prioritizes small balances (among hurdle-beating debts).")
    elif strategy == "utilization":
        notes.append("Utilization-first among debts where extra still makes economic sense.")
    elif strategy == "promo_guard":
        notes.append("Protect expiring 0% promos, then high APR.")

    if not state:
        return DebtPlanResult(
            strategy=strategy,
            as_of=as_of,
            extra_monthly=extra,
            order=order,
            total_balance=ZERO,
            total_min_payments=ZERO,
            estimated_months=0,
            estimated_interest=ZERO,
            notes=notes + ["No balances to pay down."],
            opportunity_rate=opportunity_rate,
            opportunity_rate_source=opportunity_rate_source,
            opportunity_cost_aware=opportunity_cost_aware,
            invest_vs_debt=ivd,
        )

    # Extra targets: only recommendation extra_ok (or all if not opportunity-aware)
    extra_targets = [
        o.account_id
        for o in order
        if o.account_id in state
        and (
            not opportunity_cost_aware
            or o.recommendation in ("extra_ok", "promo_watch")
            and o.beats_opportunity
            or o.recommendation == "promo_watch"
        )
    ]
    # promo_watch: only apply extra near end — for sim, still allow in last 60 days
    # For simplicity: promo_watch allowed in extra_targets only if days_left <= 60
    refined = []
    for o in order:
        if o.account_id not in state:
            continue
        if not opportunity_cost_aware:
            refined.append(o.account_id)
            continue
        if o.recommendation == "minimum_only" or not o.beats_opportunity:
            if o.recommendation != "promo_watch":
                continue
        if o.recommendation == "promo_watch" and o.promo_days_left is not None and o.promo_days_left > 60:
            continue  # don't dump extra into deep 0% early
        if o.recommendation == "promo_watch" or o.beats_opportunity:
            refined.append(o.account_id)
    target_ids = refined if refined else []

    # If extra has nowhere to go, note it
    extra_parked = ZERO
    if extra > ZERO and not target_ids:
        extra_parked = extra
        notes.append(
            f"${extra} extra/mo has no debt above the hurdle — keep it in yield "
            f"({opportunity_rate_source or 'cash'}) instead of prepaying cheap debt."
        )

    interest_total = ZERO
    months = 0
    summary: list[dict[str, Any]] = []
    cursor = as_of

    # For opportunity-aware sim: below-hurdle debts only get mins forever (never forced pay-off with extra)
    min_only_ids = {
        o.account_id
        for o in order
        if o.recommendation == "minimum_only" or (opportunity_cost_aware and not o.beats_opportunity and o.recommendation != "promo_watch")
    }

    while state and months < max_months:
        months += 1
        for sid, s in list(state.items()):
            d: DebtView = s["debt"]
            s["apr"] = effective_apr(d, cursor)
            interest = monthly_interest(s["balance"], s["apr"])
            s["balance"] += interest
            interest_total += interest

        for sid, s in list(state.items()):
            pay = min(s["min"], s["balance"])
            s["balance"] -= pay
            if s["balance"] <= Decimal("0.01"):
                summary.append(
                    {
                        "month": months,
                        "date": cursor.isoformat(),
                        "event": "paid_off",
                        "account_id": sid,
                        "name": s["name"],
                    }
                )
                del state[sid]

        remaining_extra = extra
        # Recompute promo_watch eligibility as time passes
        for tid in list(target_ids):
            if remaining_extra <= ZERO:
                break
            if tid not in state:
                continue
            if tid in min_only_ids:
                continue
            s = state[tid]
            d = s["debt"]
            eapr = effective_apr(d, cursor)
            if (
                opportunity_cost_aware
                and opportunity_rate is not None
                and eapr <= _d(opportunity_rate)
            ):
                # still in 0% deep promo or below hurdle
                if not (
                    _promo_active(d, cursor)
                    and d.promo_end_date
                    and (d.promo_end_date - cursor).days <= 60
                ):
                    continue
            pay = min(remaining_extra, s["balance"])
            s["balance"] -= pay
            remaining_extra -= pay
            if s["balance"] <= Decimal("0.01"):
                summary.append(
                    {
                        "month": months,
                        "date": cursor.isoformat(),
                        "event": "paid_off",
                        "account_id": tid,
                        "name": s["name"],
                        "note": "cleared with extra focus",
                    }
                )
                del state[tid]

        # Remaining extra not applied = parked in yield (conceptual)
        if remaining_extra > ZERO and opportunity_cost_aware:
            extra_parked = max(extra_parked, remaining_extra)

        # Stop if only min-only debts remain (can't clear them with extra by design)
        if state and all(sid in min_only_ids for sid in state):
            notes.append(
                "Remaining debts are below the opportunity hurdle — simulation keeps minimums only "
                "(not forced payoff). That is intentional (e.g. 2.625% mortgage vs 6% cash)."
            )
            break

        cursor = add_months(cursor, 1)

    if state and not all(sid in min_only_ids for sid in state):
        notes.append(f"Simulation hit {max_months} month cap with balances remaining.")

    # Months until high-rate debts cleared
    done_months = months if not state or all(sid in min_only_ids for sid in state) else None

    return DebtPlanResult(
        strategy=strategy,
        as_of=as_of,
        extra_monthly=extra,
        order=order,
        schedule_summary=summary[:40],
        total_balance=total_bal.quantize(Decimal("0.01")),
        total_min_payments=total_min.quantize(Decimal("0.01")),
        estimated_months=done_months,
        estimated_interest=interest_total.quantize(Decimal("0.01")),
        notes=notes,
        opportunity_rate=opportunity_rate,
        opportunity_rate_source=opportunity_rate_source,
        opportunity_cost_aware=opportunity_cost_aware,
        extra_to_yield_not_debt=extra_parked.quantize(Decimal("0.01")),
        invest_vs_debt=ivd,
    )


def _pct(rate: Decimal) -> str:
    return f"{float(_d(rate))*100:.3f}%"


def compare_strategies(
    debts: list[DebtView],
    *,
    extra_monthly: Decimal = ZERO,
    as_of: date | None = None,
    opportunity_rate: Decimal | None = None,
    opportunity_rate_source: str | None = None,
    opportunity_cost_aware: bool = True,
) -> list[dict[str, Any]]:
    as_of = as_of or date.today()
    results = []
    for strat in ("avalanche", "snowball", "promo_guard", "utilization", "custom"):
        plan = simulate_payoff(
            debts,
            strategy=strat,  # type: ignore[arg-type]
            extra_monthly=extra_monthly,
            as_of=as_of,
            opportunity_rate=opportunity_rate,
            opportunity_rate_source=opportunity_rate_source,
            opportunity_cost_aware=opportunity_cost_aware,
        )
        first_extra = next((o for o in plan.order if o.beats_opportunity), None)
        results.append(
            {
                "strategy": strat,
                "estimated_months": plan.estimated_months,
                "estimated_interest": str(plan.estimated_interest),
                "first_extra_target": first_extra.name if first_extra else None,
                "first_reason": first_extra.reason if first_extra else "No debt beats opportunity hurdle",
                "extra_to_yield": str(plan.extra_to_yield_not_debt),
            }
        )
    results.sort(
        key=lambda r: (
            Decimal(r["estimated_interest"]),
            r["estimated_months"] if r["estimated_months"] is not None else 9999,
        )
    )
    return results


def plan_to_dict(plan: DebtPlanResult) -> dict[str, Any]:
    return {
        "strategy": plan.strategy,
        "as_of": plan.as_of.isoformat(),
        "extra_monthly": str(plan.extra_monthly),
        "total_balance": str(plan.total_balance),
        "total_min_payments": str(plan.total_min_payments),
        "estimated_months": plan.estimated_months,
        "estimated_interest": str(plan.estimated_interest),
        "notes": plan.notes,
        "opportunity_rate": str(plan.opportunity_rate) if plan.opportunity_rate is not None else None,
        "opportunity_rate_pct": (
            f"{float(plan.opportunity_rate)*100:.3f}%" if plan.opportunity_rate is not None else None
        ),
        "opportunity_rate_source": plan.opportunity_rate_source,
        "opportunity_cost_aware": plan.opportunity_cost_aware,
        "extra_to_yield_not_debt": str(plan.extra_to_yield_not_debt),
        "invest_vs_debt": plan.invest_vs_debt,
        "order": [
            {
                "rank": o.strategy_rank,
                "account_id": o.account_id,
                "name": o.name,
                "balance": str(o.balance),
                "effective_apr": str(o.effective_apr),
                "effective_apr_pct": f"{float(o.effective_apr)*100:.3f}%",
                "min_payment": str(o.min_payment),
                "utilization_pct": str(o.utilization_pct) if o.utilization_pct is not None else None,
                "monthly_interest_est": str(o.monthly_interest_est),
                "promo_days_left": o.promo_days_left,
                "reason": o.reason,
                "beats_opportunity": o.beats_opportunity,
                "spread_vs_opportunity_pp": (
                    f"{float(o.spread_vs_opportunity)*100:.3f}"
                    if o.spread_vs_opportunity is not None
                    else None
                ),
                "recommendation": o.recommendation,
            }
            for o in plan.order
        ],
        "payoff_events": plan.schedule_summary,
    }
