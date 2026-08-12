"""Educational credit-health simulation inspired by VantageScore factor weights.

NOT an official VantageScore, FICO, or bureau score.
Uses only data HonestSpend knows (accounts, balances, user-supplied history inputs).

VantageScore 3.0 public factor weights (approximate educational model):
  Payment history   40%
  Depth of credit   21%
  Credit utilization 20%
  Balances          11%
  Recent credit      5%
  Available credit   3%
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from honestspend.services.debt_strategy import DebtView, utilization

ZERO = Decimal("0")

# VantageScore 3.0-style weights (public marketing figures)
W_PAYMENT = Decimal("0.40")
W_DEPTH = Decimal("0.21")
W_UTIL = Decimal("0.20")
W_BALANCES = Decimal("0.11")
W_RECENT = Decimal("0.05")
W_AVAILABLE = Decimal("0.03")


@dataclass
class CreditProfileInputs:
    """User-supplied factors we cannot see from cards alone."""

    on_time_rate: Decimal = Decimal("1.0")  # 0–1
    late_30_12m: int = 0
    late_60_12m: int = 0
    late_90_plus_12m: int = 0
    hard_inquiries_12m: int = 0
    new_accounts_12m: int = 0
    # Optional manual bureau score for comparison (not used in calc unless display)
    reported_vantage: int | None = None


@dataclass
class FactorScore:
    name: str
    weight: Decimal
    score_0_100: Decimal
    weighted: Decimal
    detail: str
    tips: list[str] = field(default_factory=list)


@dataclass
class CreditSimResult:
    model: str
    disclaimer: str
    score: int  # 300–850 scale educational
    band: str
    factors: list[FactorScore]
    utilization_overall: Decimal
    total_revolving_balance: Decimal
    total_revolving_limit: Decimal
    available_credit: Decimal
    suggestions: list[str]
    what_if: list[dict[str, Any]] = field(default_factory=list)


def _clamp(x: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, x))


def _band(score: int) -> str:
    if score >= 781:
        return "Excellent"
    if score >= 661:
        return "Good"
    if score >= 601:
        return "Fair"
    if score >= 500:
        return "Poor"
    return "Very poor"


def _payment_score(inp: CreditProfileInputs) -> tuple[Decimal, str, list[str]]:
    rate = _clamp(_d(inp.on_time_rate), ZERO, Decimal("1"))
    base = rate * Decimal("100")
    # Penalties for recent lates
    base -= Decimal(inp.late_30_12m) * Decimal("8")
    base -= Decimal(inp.late_60_12m) * Decimal("15")
    base -= Decimal(inp.late_90_plus_12m) * Decimal("25")
    base = _clamp(base, ZERO, Decimal("100"))
    tips = []
    if base < Decimal("95"):
        tips.append("Autopay at least the minimum before due date on every account.")
    if inp.late_30_12m or inp.late_60_12m or inp.late_90_plus_12m:
        tips.append("Time heals lates — keep a clean 12+ months going forward.")
    detail = f"On-time rate {float(rate)*100:.0f}%; lates 30/60/90: {inp.late_30_12m}/{inp.late_60_12m}/{inp.late_90_plus_12m}"
    return base, detail, tips


def _depth_score(debts: list[DebtView], as_of: date) -> tuple[Decimal, str, list[str]]:
    ages: list[int] = []
    for d in debts:
        if d.opened_date:
            ages.append(max(0, (as_of - d.opened_date).days // 30))
    tips = []
    if not ages:
        # Unknown age — neutral-low with tip to set opened dates
        tips.append("Set account opened dates on each card/loan for a better depth estimate.")
        return Decimal("55"), "Account ages not set — neutral estimate", tips
    oldest = max(ages)
    avg = sum(ages) / len(ages)
    # Map: 0 mo → 20, 24 mo avg → ~70, 84+ oldest → high
    score = Decimal("20")
    score += min(Decimal("40"), Decimal(oldest) / Decimal("84") * Decimal("40"))
    score += min(Decimal("40"), Decimal(str(avg)) / Decimal("48") * Decimal("40"))
    score = _clamp(score, ZERO, Decimal("100"))
    if oldest < 24:
        tips.append("Avoid closing your oldest card — length of history matters.")
    detail = f"Oldest ~{oldest} mo · avg age ~{avg:.0f} mo · {len(ages)} accounts with age"
    return score, detail, tips


def _util_score(debts: list[DebtView]) -> tuple[Decimal, str, list[str], Decimal, Decimal, Decimal]:
    bal = ZERO
    lim = ZERO
    tips = []
    for d in debts:
        if d.kind != "credit":
            continue
        bal += max(ZERO, _d(d.balance))
        lim += max(ZERO, _d(d.credit_limit))
    if lim <= ZERO:
        tips.append("Add credit limits on cards to track utilization accurately.")
        return Decimal("50"), "No revolving limits on file", tips, bal, lim, ZERO

    util = bal / lim * Decimal("100")
    # Scoring curve: 0–9% excellent, <30% good, 50%+ poor, 100% terrible
    if util <= Decimal("9"):
        score = Decimal("98")
    elif util <= Decimal("29"):
        score = Decimal("85") - (util - Decimal("9")) * Decimal("0.8")
    elif util <= Decimal("49"):
        score = Decimal("65") - (util - Decimal("29")) * Decimal("1.2")
    elif util <= Decimal("74"):
        score = Decimal("40") - (util - Decimal("49")) * Decimal("0.8")
    else:
        score = Decimal("20") - min(Decimal("20"), (util - Decimal("74")) * Decimal("0.3"))
    score = _clamp(score, ZERO, Decimal("100"))
    avail = lim - bal
    if util > Decimal("30"):
        target = lim * Decimal("0.30")
        pay_down = max(ZERO, bal - target)
        tips.append(f"Pay down ~${pay_down:.0f} to get under 30% overall utilization.")
    if util > Decimal("9"):
        tips.append("Under ~10% utilization is typically strongest for score models.")
    # Per-card hot spots
    for d in debts:
        u = utilization(d)
        if u is not None and u > Decimal("50"):
            tips.append(f"{d.name} is at {u}% util — prioritize that card for score impact.")
    detail = f"Overall utilization {util.quantize(Decimal('0.1'))}% (${bal:.0f} / ${lim:.0f})"
    return score, detail, tips, bal, lim, avail


def _balances_score(debts: list[DebtView]) -> tuple[Decimal, str, list[str]]:
    total = sum((max(ZERO, _d(d.balance)) for d in debts), ZERO)
    revolving = sum(
        (max(ZERO, _d(d.balance)) for d in debts if d.kind == "credit"), ZERO
    )
    tips = []
    # Lower balances better; rough log-ish scale for personal finance education
    if revolving <= ZERO:
        score = Decimal("95")
    elif revolving < Decimal("1000"):
        score = Decimal("85")
    elif revolving < Decimal("5000"):
        score = Decimal("70")
    elif revolving < Decimal("15000"):
        score = Decimal("50")
    else:
        score = Decimal("30")
    if revolving > Decimal("5000"):
        tips.append("Lower revolving balances improve both score factors and IFPP cash.")
    detail = f"Revolving ${revolving:.0f} · all tracked debt ${total:.0f}"
    return score, detail, tips


def _recent_score(inp: CreditProfileInputs) -> tuple[Decimal, str, list[str]]:
    inq = inp.hard_inquiries_12m
    new_a = inp.new_accounts_12m
    score = Decimal("100")
    score -= Decimal(inq) * Decimal("12")
    score -= Decimal(new_a) * Decimal("10")
    score = _clamp(score, ZERO, Decimal("100"))
    tips = []
    if inq >= 3:
        tips.append("Rate-shop within short windows; avoid extra hard pulls for 6–12 months.")
    if new_a >= 2:
        tips.append("Pause new credit lines until recent accounts age.")
    detail = f"Hard inquiries (12m): {inq} · new accounts (12m): {new_a}"
    return score, detail, tips


def _available_score(available: Decimal, limit: Decimal) -> tuple[Decimal, str, list[str]]:
    tips = []
    if limit <= ZERO:
        return Decimal("50"), "Limits unknown", tips
    ratio = available / limit
    score = _clamp(ratio * Decimal("100"), ZERO, Decimal("100"))
    if ratio < Decimal("0.5"):
        tips.append("More available credit (lower balances or higher limits) helps slightly.")
    detail = f"Available ${available:.0f} of ${limit:.0f} ({float(ratio)*100:.0f}%)"
    return score, detail, tips


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def simulate_credit(
    debts: list[DebtView],
    profile: CreditProfileInputs | None = None,
    *,
    as_of: date | None = None,
) -> CreditSimResult:
    as_of = as_of or date.today()
    profile = profile or CreditProfileInputs()

    factors: list[FactorScore] = []
    tips_all: list[str] = []

    p_s, p_d, p_t = _payment_score(profile)
    factors.append(FactorScore("Payment history", W_PAYMENT, p_s, p_s * W_PAYMENT, p_d, p_t))
    tips_all.extend(p_t)

    d_s, d_d, d_t = _depth_score(debts, as_of)
    factors.append(FactorScore("Depth of credit", W_DEPTH, d_s, d_s * W_DEPTH, d_d, d_t))
    tips_all.extend(d_t)

    u_s, u_d, u_t, bal, lim, avail = _util_score(debts)
    factors.append(FactorScore("Credit utilization", W_UTIL, u_s, u_s * W_UTIL, u_d, u_t))
    tips_all.extend(u_t)

    b_s, b_d, b_t = _balances_score(debts)
    factors.append(FactorScore("Balances", W_BALANCES, b_s, b_s * W_BALANCES, b_d, b_t))
    tips_all.extend(b_t)

    r_s, r_d, r_t = _recent_score(profile)
    factors.append(FactorScore("Recent credit", W_RECENT, r_s, r_s * W_RECENT, r_d, r_t))
    tips_all.extend(r_t)

    a_s, a_d, a_t = _available_score(avail, lim)
    factors.append(FactorScore("Available credit", W_AVAILABLE, a_s, a_s * W_AVAILABLE, a_d, a_t))
    tips_all.extend(a_t)

    composite = sum((f.weighted for f in factors), ZERO)  # 0–100
    # Map 0–100 → 300–850
    score = int(300 + float(composite) / 100.0 * 550)
    score = max(300, min(850, score))

    util_pct = (bal / lim * Decimal("100")).quantize(Decimal("0.1")) if lim > ZERO else ZERO

    what_if = _what_if_scenarios(debts, profile, as_of, score)

    # Dedupe tips
    seen = set()
    suggestions = []
    for t in tips_all:
        if t not in seen:
            seen.add(t)
            suggestions.append(t)

    return CreditSimResult(
        model="HonestSpend educational model (VantageScore 3.0-style weights)",
        disclaimer=(
            "This is NOT an official VantageScore, FICO, Equifax, Experian, or TransUnion score. "
            "Credit Karma and the bureaus do not provide consumer APIs for third-party apps. "
            "This simulation uses only debts and history you track in HonestSpend for planning."
        ),
        score=score,
        band=_band(score),
        factors=factors,
        utilization_overall=util_pct,
        total_revolving_balance=bal,
        total_revolving_limit=lim,
        available_credit=avail,
        suggestions=suggestions[:12],
        what_if=what_if,
    )


def _what_if_scenarios(
    debts: list[DebtView],
    profile: CreditProfileInputs,
    as_of: date,
    baseline: int,
) -> list[dict[str, Any]]:
    out = []
    # Pay to 30% util
    bal = sum((max(ZERO, _d(d.balance)) for d in debts if d.kind == "credit"), ZERO)
    lim = sum((max(ZERO, _d(d.credit_limit)) for d in debts if d.kind == "credit"), ZERO)
    if lim > ZERO and bal > lim * Decimal("0.30"):
        target = lim * Decimal("0.30")
        pay = bal - target
        adj = []
        remaining = pay
        for d in sorted(debts, key=lambda x: -max(ZERO, _d(x.balance))):
            if d.kind != "credit" or remaining <= ZERO:
                continue
            cut = min(remaining, max(ZERO, _d(d.balance)))
            if cut <= ZERO:
                continue
            adj.append((d.id, cut))
            remaining -= cut
        sim_debts = _apply_balance_cuts(debts, adj)
        s = simulate_credit(sim_debts, profile, as_of=as_of)
        out.append(
            {
                "label": f"Pay down ${pay:.0f} to ≤30% utilization",
                "score": s.score,
                "delta": s.score - baseline,
            }
        )
    if lim > ZERO and bal > lim * Decimal("0.10"):
        target = lim * Decimal("0.10")
        pay = bal - target
        adj = []
        remaining = pay
        for d in sorted(debts, key=lambda x: -max(ZERO, _d(x.balance))):
            if d.kind != "credit" or remaining <= ZERO:
                continue
            cut = min(remaining, max(ZERO, _d(d.balance)))
            if cut <= ZERO:
                continue
            adj.append((d.id, cut))
            remaining -= cut
        sim_debts = _apply_balance_cuts(debts, adj)
        s = simulate_credit(sim_debts, profile, as_of=as_of)
        out.append(
            {
                "label": f"Pay down ${pay:.0f} to ≤10% utilization",
                "score": s.score,
                "delta": s.score - baseline,
            }
        )
    # Perfect on-time
    if profile.on_time_rate < Decimal("1") or profile.late_30_12m:
        p2 = CreditProfileInputs(
            on_time_rate=Decimal("1"),
            late_30_12m=0,
            late_60_12m=0,
            late_90_plus_12m=0,
            hard_inquiries_12m=profile.hard_inquiries_12m,
            new_accounts_12m=profile.new_accounts_12m,
        )
        s = simulate_credit(debts, p2, as_of=as_of)
        out.append(
            {
                "label": "12 months perfect on-time (no new lates)",
                "score": s.score,
                "delta": s.score - baseline,
                "note": "Illustrative; model cannot age off historical lates precisely",
            }
        )
    return out


def _apply_balance_cuts(debts: list[DebtView], cuts: list[tuple[int, Decimal]]) -> list[DebtView]:
    cut_map = {i: c for i, c in cuts}
    out = []
    for d in debts:
        if d.id in cut_map:
            out.append(
                DebtView(
                    id=d.id,
                    name=d.name,
                    kind=d.kind,
                    balance=max(ZERO, _d(d.balance) - cut_map[d.id]),
                    apr=d.apr,
                    min_payment=d.min_payment,
                    credit_limit=d.credit_limit,
                    promo_apr=d.promo_apr,
                    promo_end_date=d.promo_end_date,
                    promo_balance=d.promo_balance,
                    payment_due_day=d.payment_due_day,
                    priority_rank=d.priority_rank,
                    opened_date=d.opened_date,
                )
            )
        else:
            out.append(d)
    return out


def sim_to_dict(result: CreditSimResult) -> dict[str, Any]:
    return {
        "model": result.model,
        "disclaimer": result.disclaimer,
        "score": result.score,
        "band": result.band,
        "utilization_overall_pct": str(result.utilization_overall),
        "total_revolving_balance": str(result.total_revolving_balance),
        "total_revolving_limit": str(result.total_revolving_limit),
        "available_credit": str(result.available_credit),
        "factors": [
            {
                "name": f.name,
                "weight_pct": f"{float(f.weight)*100:.0f}%",
                "score_0_100": float(f.score_0_100.quantize(Decimal('0.1'))),
                "weighted": float(f.weighted.quantize(Decimal('0.01'))),
                "detail": f.detail,
                "tips": f.tips,
            }
            for f in result.factors
        ],
        "suggestions": result.suggestions,
        "what_if": result.what_if,
    }
