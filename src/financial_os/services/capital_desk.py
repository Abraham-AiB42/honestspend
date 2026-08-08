"""Capital desk: where should the next dollar go?

Priority (PRODUCT.md — fiscal soundness first):
  1. Prevent checking going negative / cover red-day gap
  2. Avoid stupid fees (due cards, overdraft risk)
  3. Clear expiring 0% balloons (near end)
  4. High-APR debt above opportunity hurdle
  5. Top up safety buffer
  6. Park in yield (cash APY)
  7. Cheap debt below hurdle — minimum only (do not prepay)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, AppSettings, Transaction
from financial_os.services.debt_service import accounts_to_debts, opportunity_context
from financial_os.services.debt_strategy import effective_apr, simulate_payoff
from financial_os.services.ifpp_service import run_ifpp

ZERO = Decimal("0")


@dataclass
class AllocationStep:
    rank: int
    action: str
    title: str
    amount_hint: str
    reason: str
    alternatives: list[str] = field(default_factory=list)
    priority: str = "fiscal"  # fiscal | credit | optional


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _fee_warnings(session: Session, as_of: date) -> list[str]:
    """Detect fee-like activity in recent ledger."""
    from datetime import timedelta

    start = as_of - timedelta(days=45)
    txns = (
        session.query(Transaction)
        .filter(Transaction.txn_date >= start, Transaction.amount < 0)
        .limit(500)
        .all()
    )
    warnings = []
    fee_words = (
        "fee",
        "overdraft",
        "nsf",
        "late fee",
        "interest charge",
        "finance charge",
        "annual fee",
        "foreign transaction",
        "cash advance",
    )
    total_fees = ZERO
    hits = 0
    for t in txns:
        blob = f"{t.payee or ''} {t.memo or ''}".lower()
        if any(w in blob for w in fee_words):
            hits += 1
            total_fees += abs(_d(t.amount))
    if hits:
        warnings.append(
            f"Detected ~{hits} fee-like transactions (~${total_fees:.2f}) in last 45 days — "
            f"fees are avoidable drag on IFPP."
        )
    return warnings


def build_capital_desk(
    session: Session,
    *,
    as_of: date | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    settings = session.get(AppSettings, 1) or AppSettings(id=1)
    ifpp = run_ifpp(session, as_of=as_of, profile_id=profile_id, scope=scope)
    opp_rate, opp_src, opp_aware = opportunity_context(session)
    debts = accounts_to_debts(session)
    extra = _d(getattr(settings, "debt_extra_monthly", None) or 0)
    plan = simulate_payoff(
        debts,
        strategy=(getattr(settings, "debt_strategy", None) or "avalanche"),  # type: ignore[arg-type]
        extra_monthly=extra,
        as_of=as_of,
        opportunity_rate=opp_rate,
        opportunity_rate_source=opp_src,
        opportunity_cost_aware=opp_aware,
    )

    steps: list[AllocationStep] = []
    rank = 1

    # 1. Red day / checking safety
    if ifpp.next_red_day is not None or ifpp.cash_spendable <= ZERO:
        gap = "cash runway"
        if ifpp.cash_spendable <= ZERO:
            gap = "Spendable is $0 or less after buffer and bills"
        else:
            gap = f"Projected red day {ifpp.next_red_day.isoformat()}"
        steps.append(
            AllocationStep(
                rank=rank,
                action="protect_checking",
                title="Protect checking — never go negative",
                amount_hint="Cover upcoming outflows before any discretionary spend",
                reason=gap,
                alternatives=[
                    "Delay non-essential purchases",
                    "Move money from yield/savings into checking before bill hits",
                    "Use interest-free card float only if full-pay path exists",
                ],
                priority="fiscal",
            )
        )
        rank += 1

    # 2. Fee warnings
    for fw in _fee_warnings(session, as_of):
        steps.append(
            AllocationStep(
                rank=rank,
                action="stop_fees",
                title="Stop fee leakage",
                amount_hint="Fix autopay / minimums / overdraft settings",
                reason=fw,
                alternatives=["Autopay mins from checking", "Turn off overdraft if possible"],
                priority="fiscal",
            )
        )
        rank += 1

    # 2b. Tax vault set-aside if enabled and thin
    from financial_os.services.tax_vault import get_tax_vault

    vault = get_tax_vault(session)
    if vault.get("enabled") and Decimal(vault.get("balance") or 0) > 0:
        steps.append(
            AllocationStep(
                rank=rank,
                action="respect_tax_vault",
                title="Respect tax vault (already reserved)",
                amount_hint=f"${vault['balance']} not in Spendable",
                reason=vault.get("note") or "Tax reserve reduces safe-to-spend.",
                alternatives=[
                    "Add more via Tax vault on Tax tab or Settings",
                    "Release only when filing/paying estimated tax",
                ],
                priority="fiscal",
            )
        )
        rank += 1
    elif vault.get("enabled"):
        rate = vault.get("income_rate")
        hint = "Set a balance or income set-aside rate"
        if rate:
            hint = f"Suggested rate {vault.get('income_rate_pct')} of variable income"
        steps.append(
            AllocationStep(
                rank=rank,
                action="fund_tax_vault",
                title="Fund tax vault",
                amount_hint=hint,
                reason="Empty tax vault — April risk. Reserve estimated taxes so Spendable stays honest.",
                alternatives=["Disable tax vault if you track elsewhere", "Use estimated quarterly amount"],
                priority="fiscal",
            )
        )
        rank += 1

    # 3. Promo balloons nearing end
    for o in plan.order:
        if o.promo_days_left is not None and o.promo_days_left <= 60 and o.balance > ZERO:
            steps.append(
                AllocationStep(
                    rank=rank,
                    action="promo_balloon",
                    title=f"Fund 0% balloon: {o.name}",
                    amount_hint=f"~${o.balance} by promo end (~{o.promo_days_left} days)",
                    reason=(
                        f"Promo path ends soon — pay in full to avoid APR. "
                        f"Intentional 0% float is fine until then."
                    ),
                    alternatives=[
                        "Minimums only until last month, then balloon (your stated strategy)",
                        f"If cash is short, free up yield before promo ends — not after",
                    ],
                    priority="fiscal",
                )
            )
            rank += 1

    # 4. High-APR debt above opportunity hurdle
    for o in plan.order:
        if o.recommendation == "extra_ok" and o.beats_opportunity and o.effective_apr > ZERO:
            steps.append(
                AllocationStep(
                    rank=rank,
                    action="attack_apr",
                    title=f"Extra to high-cost debt: {o.name}",
                    amount_hint=f"Min ${o.min_payment}/mo + extras; APR {float(o.effective_apr)*100:.2f}%",
                    reason=o.reason,
                    alternatives=[
                        f"Park in yield only if rate ≥ {plan.opportunity_rate_pct or 'hurdle'} (it isn't for this debt)",
                        "Snowball smaller balances if you prefer motivation over pure math",
                    ],
                    priority="fiscal",
                )
            )
            rank += 1
            break  # only spotlight current #1 extra target

    # 5. Safety buffer
    buffer = _d(settings.safety_buffer or 0)
    checking_bal = sum(
        (
            _d(a.current_balance)
            for a in session.query(Account).filter(Account.kind == "checking").all()
        ),
        ZERO,
    )
    if buffer > ZERO and checking_bal < buffer:
        steps.append(
            AllocationStep(
                rank=rank,
                action="top_up_buffer",
                title="Top up safety buffer",
                amount_hint=f"${buffer - checking_bal:.2f} to reach ${buffer:.0f} buffer",
                reason=f"Default safety net is ${buffer:.0f} (user-configurable).",
                alternatives=["Lower buffer in Settings if too tight", "Build buffer after APR debt"],
                priority="fiscal",
            )
        )
        rank += 1

    # 6. Yield park
    if plan.opportunity_rate and _d(plan.opportunity_rate) > ZERO:
        steps.append(
            AllocationStep(
                rank=rank,
                action="park_yield",
                title="Park surplus in yield",
                amount_hint=f"Earn ~{plan.opportunity_rate_pct} ({plan.opportunity_rate_source})",
                reason=(
                    "After mins, promos, and high-APR debt: cash should earn. "
                    "Do not prepay debt cheaper than this yield."
                ),
                alternatives=[
                    "Manual hurdle rate in Credit & Debts if you want a different bar",
                    "Optional: pay cheap debt only for emotional/debt-free goals (override)",
                ],
                priority="fiscal",
            )
        )
        rank += 1

    # 7. Cheap debt note
    cheap = [o for o in plan.order if o.recommendation == "minimum_only"]
    if cheap:
        names = ", ".join(o.name for o in cheap[:3])
        steps.append(
            AllocationStep(
                rank=rank,
                action="min_only_cheap_debt",
                title="Minimum only on cheap debt",
                amount_hint=names,
                reason=(
                    "These cost less than your opportunity yield (e.g. mortgage 2.625% vs cash 6%). "
                    "Extra principal is usually fiscally worse than keeping cash earning."
                ),
                alternatives=[
                    "Override with custom pay rank if debt-free date is a personal goal",
                    "Turn off opportunity-cost awareness (not recommended)",
                ],
                priority="fiscal",
            )
        )
        rank += 1

    # Credit secondary tip
    high_util = [o for o in plan.order if o.utilization_pct and _d(o.utilization_pct) > 30]
    if high_util and ifpp.cash_spendable > ZERO:
        steps.append(
            AllocationStep(
                rank=rank,
                action="credit_util",
                title=f"Optional (credit #2): lower util on {high_util[0].name}",
                amount_hint=f"Util {high_util[0].utilization_pct}% — under 30%/10% helps score models",
                reason="Fiscal first is done in steps above; utilization is secondary priority.",
                alternatives=["Only after buffer + high-APR and promo safety"],
                priority="credit",
            )
        )

    if not steps:
        steps.append(
            AllocationStep(
                rank=1,
                action="hold",
                title="You're clear — preserve optionality",
                amount_hint="No urgent gap; keep mins autopaid and surplus in yield",
                reason="No red day, no fee flags, no high-APR extra target surfaced.",
                alternatives=["Pre-purchase check before big spends", "Review uncategorized queue weekly"],
                priority="optional",
            )
        )

    # Headline next $1
    head = steps[0]
    return {
        "as_of": as_of.isoformat(),
        "headline": {
            "action": head.action,
            "title": head.title,
            "amount_hint": head.amount_hint,
            "reason": head.reason,
            "alternatives": head.alternatives,
        },
        "steps": [
            {
                "rank": s.rank,
                "action": s.action,
                "title": s.title,
                "amount_hint": s.amount_hint,
                "reason": s.reason,
                "alternatives": s.alternatives,
                "priority": s.priority,
            }
            for s in steps
        ],
        "ifpp": {
            "cash_spendable": str(ifpp.cash_spendable),
            "card_float_interest_free": str(ifpp.card_float_interest_free),
            "combined_purchasing_power": str(ifpp.combined_purchasing_power),
            "next_red_day": ifpp.next_red_day.isoformat() if ifpp.next_red_day else None,
            "mode": ifpp.mode,
            "warnings": ifpp.warnings[:12],
        },
        "opportunity": {
            "rate": str(opp_rate) if opp_rate is not None else None,
            "rate_pct": f"{float(opp_rate)*100:.3f}%" if opp_rate else None,
            "source": opp_src,
            "aware": opp_aware,
        },
        "tax_vault": vault,
        "principles": [
            "Never go negative on checking",
            "Intentional 0% float OK when full-pay path exists",
            "No unnecessary interest or fees",
            "Don't prepay debt cheaper than your yield",
            "Fiscal soundness #1 · credit score #2",
        ],
    }
