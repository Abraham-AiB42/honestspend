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

# Soft steps may appear on capital desk but should not dominate Simple "Do this next"
SOFT_HEADLINE_ACTIONS = frozenset(
    {
        "fund_tax_vault",
        "respect_tax_vault",
        "park_yield",
        "min_only_cheap_debt",
        "credit_util",
        "hold",
    }
)


def _tax_vault_is_urgent(session: Session, vault: dict[str, Any]) -> bool:
    """Hard-push tax set-aside only for self-employed / business / explicit rate."""
    if not vault.get("enabled"):
        return False
    if vault.get("income_rate"):
        return True
    from financial_os.db import Profile

    if session.query(Profile).filter(Profile.entity_type == "business", Profile.archived_at.is_(None)).count():
        return True
    # Variable / self-employed style scheduled income without a linked employer paycheck name is rare;
    # presence of any positive scheduled income alone is not enough (W-2 paycheck).
    return False


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


def _fee_warnings(
    session: Session,
    as_of: date,
    *,
    profile_id: int | None = None,
) -> list[str]:
    """Detect fee-like activity via shared fee_scan heuristics."""
    from financial_os.services.fee_scan import scan_fees

    fees = scan_fees(session, days=45, limit=50, as_of=as_of, profile_id=profile_id)
    if fees["count"] <= 0:
        return []
    return [
        f"Detected ~{fees['count']} fee-like transactions (~${fees['total_abs']}) in last 45 days — "
        f"fees are avoidable drag on Safe to spend."
    ]


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
    resolved_pid = (ifpp.details or {}).get("profile_id")
    sc = (ifpp.details or {}).get("ifpp_scope") or scope or "entity"
    debts = accounts_to_debts(
        session,
        profile_id=resolved_pid if sc == "entity" else None,
        scope=sc,
    )
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
    if ifpp.is_red_now or ifpp.next_red_day is not None or ifpp.cash_spendable <= ZERO:
        if ifpp.is_red_now:
            gap = "RED NOW — checking already negative or runway underwater"
        elif ifpp.cash_spendable <= ZERO:
            gap = "Spendable is $0 or less after buffer and bills"
        else:
            gap = f"Projected red day {ifpp.next_red_day.isoformat()}"
        steps.append(
            AllocationStep(
                rank=rank,
                action="protect_checking",
                title="Protect checking — never go negative",
                amount_hint="Cover gap before any discretionary spend; open rescue options",
                reason=gap,
                alternatives=[
                    "Open Rescue options on Home",
                    "Delay non-essential purchases",
                    "Move money from savings into checking before the bill hits",
                    "Use interest-free card float only if you can pay in full",
                ],
                priority="fiscal",
            )
        )
        rank += 1

    # 2. Fee warnings
    for fw in _fee_warnings(
        session,
        as_of,
        profile_id=resolved_pid if sc == "entity" else None,
    ):
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

    # 2b. Tax vault — only hard-push when self-employed / business / rate set.
    # Personal W-2 first-run should not open on "Fund tax vault" (stupid-simple).
    from financial_os.services.tax_vault import get_tax_vault

    vault = get_tax_vault(session)
    tax_step: AllocationStep | None = None
    tax_urgent = _tax_vault_is_urgent(session, vault)
    if vault.get("enabled") and Decimal(vault.get("balance") or 0) > 0:
        tax_step = AllocationStep(
            rank=0,  # filled when inserted
            action="respect_tax_vault",
            title="Taxes already set aside",
            amount_hint=f"${vault['balance']} kept out of Safe to spend",
            reason=vault.get("note")
            or "Tax reserve is already reducing Safe to spend — leave it alone until you file.",
            alternatives=[
                "Add more under Full books → Tax vault",
                "Release only when paying estimated tax",
            ],
            priority="optional" if not tax_urgent else "fiscal",
        )
    elif vault.get("enabled"):
        rate = vault.get("income_rate")
        hint = "Set a balance or income set-aside rate"
        if rate:
            hint = f"Suggested rate {vault.get('income_rate_pct')} of variable income"
        if tax_urgent:
            tax_step = AllocationStep(
                rank=0,
                action="fund_tax_vault",
                title="Set aside money for taxes",
                amount_hint=hint,
                reason=(
                    "Business or self-employed income usually needs estimated taxes. "
                    "Reserve so Safe to spend stays honest."
                ),
                alternatives=[
                    "Turn off tax set-aside if you track elsewhere",
                    "Use your quarterly estimate",
                ],
                priority="fiscal",
            )
        else:
            # Soft optional — inserted after hard fiscal work so it never headlines cold start
            tax_step = AllocationStep(
                rank=0,
                action="fund_tax_vault",
                title="Optional: set aside for taxes",
                amount_hint=hint,
                reason=(
                    "If any income isn't tax-withheld, park an estimate so Safe to spend "
                    "doesn't tempt April surprises. Skip if your paycheck already withholds."
                ),
                alternatives=[
                    "Skip — paycheck already withholds",
                    "Turn off tax set-aside under Full books → Tax",
                ],
                priority="optional",
            )

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

    # Tax vault: urgent fiscal → now; optional → after hard fiscal (before buffer polish)
    if tax_step is not None and tax_step.priority == "fiscal":
        tax_step.rank = rank
        steps.append(tax_step)
        rank += 1

    # 5. Safety buffer
    buffer = _d(settings.safety_buffer or 0)
    cq = session.query(Account).filter(Account.kind == "checking", Account.archived_at.is_(None))
    if sc == "entity" and resolved_pid is not None:
        cq = cq.filter(Account.profile_id == resolved_pid)
    checking_bal = sum((_d(a.current_balance) for a in cq.all()), ZERO)
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

    # Optional tax set-aside (personal W-2) — after hard fiscal, before yield polish
    if tax_step is not None and tax_step.priority == "optional":
        tax_step.rank = rank
        steps.append(tax_step)
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
        rank += 1

    # Wealth basics (educational) — only when fiscal house is not on fire
    has_critical = bool(ifpp.is_red_now) or any(
        s.action in ("protect_checking", "stop_fees") for s in steps[:3]
    )
    if not has_critical and ifpp.cash_spendable > _d("200"):
        from financial_os.services.wealth_basics import build_wealth_tips

        for tip in build_wealth_tips(
            session,
            cash_spendable=ifpp.cash_spendable,
            is_red_now=bool(ifpp.is_red_now),
            has_critical_fiscal=has_critical,
        ):
            steps.append(
                AllocationStep(
                    rank=rank,
                    action=tip["action"],
                    title=tip["title"],
                    amount_hint=tip["amount_hint"],
                    reason=tip["reason"]
                    + (
                        f" ({tip['disclaimer']})"
                        if tip.get("disclaimer")
                        else ""
                    ),
                    alternatives=tip.get("alternatives") or [],
                    priority="wealth",
                )
            )
            rank += 1

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

    # Headline: fiscal first, then wealth, never lead with soft optional nags
    head = next((s for s in steps if s.priority == "fiscal"), None)
    if head is None:
        head = next((s for s in steps if s.priority == "wealth"), None)
    if head is None:
        head = steps[0]
    return {
        "as_of": as_of.isoformat(),
        "headline": {
            "action": head.action,
            "title": head.title,
            "amount_hint": head.amount_hint,
            "reason": head.reason,
            "alternatives": head.alternatives,
            "priority": head.priority,
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
            "is_red_now": bool(ifpp.is_red_now),
            "mode": ifpp.mode,
            "warnings": ifpp.warnings[:12],
            "ifpp_scope": sc,
            "profile_id": resolved_pid,
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
