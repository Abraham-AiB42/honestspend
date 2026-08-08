"""Interest-Free Purchasing Power (IFPP) engine.

Pure functions — no DB I/O. Perfect for unit tests and the trust bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

ZERO = Decimal("0")


@dataclass(frozen=True)
class CashAccountView:
    id: int
    name: str
    balance: Decimal
    is_cash_for_ifpp: bool = True
    kind: str = "checking"  # checking | savings | cash | other


@dataclass(frozen=True)
class CardView:
    id: int
    name: str
    balance: Decimal  # amount owed (positive = you owe)
    credit_limit: Decimal
    available_credit: Decimal
    statement_close_day: int | None
    payment_due_day: int | None
    apr: Decimal | None = None
    promo_apr: Decimal | None = None
    promo_end_date: date | None = None
    promo_balance: Decimal | None = None
    min_payment: Decimal | None = None
    rewards_score: int = 0  # higher = better rewards; only used after safety


@dataclass(frozen=True)
class ScheduledView:
    id: int
    name: str
    amount: Decimal  # + inflow, - outflow
    on_date: date
    certainty: Literal["fixed", "expected", "historical_avg"] = "fixed"


@dataclass
class CardPlan:
    account_id: int
    name: str
    safe_to_charge: Decimal
    payoff_path: str
    risk: str
    utilization_pct: Decimal
    warnings: list[str] = field(default_factory=list)
    rewards_rank_eligible: bool = False


@dataclass
class IfppResult:
    as_of: date
    mode: str
    cash_spendable: Decimal
    card_float_interest_free: Decimal
    combined_purchasing_power: Decimal
    cards: list[CardPlan]
    next_red_day: date | None
    warnings: list[str]
    details: dict


def _d(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _certainty_weight(certainty: str, mode: str) -> Decimal:
    if mode == "conservative":
        # Only fixed, already-like income counts — and only if amount > 0 we still
        # require certainty fixed. Expected/historical ignored in conservative.
        return Decimal("1") if certainty == "fixed" else ZERO
    # expected mode
    return {
        "fixed": Decimal("1"),
        "expected": Decimal("0.85"),
        "historical_avg": Decimal("0.70"),
    }.get(certainty, ZERO)


def next_due_date(as_of: date, due_day: int | None) -> date | None:
    if not due_day or due_day < 1 or due_day > 31:
        return None
    year, month = as_of.year, as_of.month
    # try this month
    for _ in range(3):
        day = min(due_day, _days_in_month(year, month))
        candidate = date(year, month, day)
        if candidate >= as_of:
            return candidate
        month += 1
        if month > 12:
            month = 1
            year += 1
    return None


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def is_promo_active(card: CardView, as_of: date) -> bool:
    if card.promo_end_date is None:
        return False
    if card.promo_end_date < as_of:
        return False
    if card.promo_apr is None:
        return False
    return _d(card.promo_apr) == ZERO


def compute_cash_spendable(
    cash_accounts: list[CashAccountView],
    schedule: list[ScheduledView],
    *,
    as_of: date,
    mode: str,
    safety_buffer: Decimal,
    horizon_days: int = 45,
    never_negative_scope: str = "checking",
    tax_vault: Decimal = ZERO,
) -> tuple[Decimal, date | None, list[str]]:
    """Cash you can spend without going negative under scheduled reality.

    never_negative_scope:
      checking — only checking accounts (product default; never bounce checking)
      all_cash — all is_cash_for_ifpp accounts
    tax_vault — reserved for taxes; reduces spendable (not investable product)
    """
    warnings: list[str] = []
    scope = (never_negative_scope or "checking").lower()
    if scope == "checking":
        pool = [
            a
            for a in cash_accounts
            if a.is_cash_for_ifpp and a.kind == "checking"
        ]
        if not pool:
            # fall back if user only marked savings as IFPP
            pool = [a for a in cash_accounts if a.is_cash_for_ifpp]
            if pool:
                warnings.append(
                    "No checking accounts in IFPP pool — using other cash-flagged accounts. "
                    "Add a checking account for never-neg checking enforcement."
                )
    else:
        pool = [a for a in cash_accounts if a.is_cash_for_ifpp]

    cash = sum((_d(a.balance) for a in pool), ZERO)
    # Hard warning if raw checking already negative
    for a in cash_accounts:
        if a.kind == "checking" and _d(a.balance) < ZERO:
            warnings.append(
                f"CHECKING NEGATIVE: {a.name} is ${_d(a.balance):.2f} — resolve before anything else."
            )
    cash -= _d(safety_buffer)
    vault = max(ZERO, _d(tax_vault))
    if vault > ZERO:
        cash -= vault
        warnings.append(
            f"Tax vault reserves ${vault:.2f} — not available to spend (April problem avoided)."
        )

    horizon_end = as_of + timedelta(days=horizon_days)
    events = [s for s in schedule if as_of <= s.on_date <= horizon_end]
    events.sort(key=lambda s: s.on_date)

    running = cash
    min_running = cash
    red_day: date | None = None

    for ev in events:
        weight = _certainty_weight(ev.certainty, mode)
        if ev.amount > 0 and weight == ZERO:
            continue  # ignore uncertain income in conservative
        delta = _d(ev.amount) * weight if ev.amount > 0 else _d(ev.amount)
        # Outflows always full (bills are real)
        if ev.amount < 0:
            delta = _d(ev.amount)
        running += delta
        if running < min_running:
            min_running = running
        if running < ZERO and red_day is None:
            red_day = ev.on_date
            warnings.append(
                f"Projected negative on {ev.on_date.isoformat()} after '{ev.name}' "
                f"(balance would be {running})"
            )

    spendable = max(ZERO, min_running)
    return spendable, red_day, warnings


def card_safe_to_charge(
    card: CardView,
    *,
    as_of: date,
    cash_available_for_payoff: Decimal,
    schedule: list[ScheduledView],
    mode: str,
    soft_util_pct: int = 10,
    hard_util_pct: int = 30,
) -> CardPlan:
    """How much can go on this card without interest/fees if pay-in-full path holds."""
    warnings: list[str] = []
    balance = max(ZERO, _d(card.balance))
    limit = _d(card.credit_limit)
    available = _d(card.available_credit)
    if available <= ZERO and limit > ZERO:
        available = max(ZERO, limit - balance)

    util = ZERO if limit <= ZERO else (balance / limit) * Decimal("100")
    if util >= hard_util_pct:
        warnings.append(f"Utilization {util:.1f}% ≥ hard warn {hard_util_pct}% (Vantage-style)")
    elif util >= soft_util_pct:
        warnings.append(f"Utilization {util:.1f}% ≥ soft warn {soft_util_pct}%")

    promo = is_promo_active(card, as_of)
    promo_bal = _d(card.promo_balance) if card.promo_balance is not None else ZERO
    due = next_due_date(as_of, card.payment_due_day)

    # Cash we can use to pay this card by due date (conservative path for new charges)
    cash_for_card = cash_available_for_payoff

    if promo and card.promo_end_date:
        # 0% strategy: can float promo balance until last month; new charges on promo
        # only safe if we can pay them off by promo end without APR.
        days_left = (card.promo_end_date - as_of).days
        if days_left < 0:
            promo = False
        else:
            # Minimum payments until last month still need cash runway — simplified:
            # safe_to_charge limited by available credit AND ability to clear by promo end
            # using cash + scheduled net before promo_end.
            net_before_end = _scheduled_net(schedule, as_of, card.promo_end_date, mode)
            capacity_by_cash = max(ZERO, cash_for_card + net_before_end)
            # Existing promo balance already planned for balloon; new charge capacity:
            safe = min(available, capacity_by_cash)
            path = (
                f"0% promo until {card.promo_end_date.isoformat()}; "
                f"pay minimums then balloon payoff (never revolve at APR)"
            )
            risk = "promo_float"
            if safe <= ZERO:
                warnings.append("No cash path to clear new charges by promo end")
            plan = CardPlan(
                account_id=card.id,
                name=card.name,
                safe_to_charge=safe.quantize(Decimal("0.01")),
                payoff_path=path,
                risk=risk,
                utilization_pct=util.quantize(Decimal("0.1")),
                warnings=warnings,
                rewards_rank_eligible=safe > ZERO,
            )
            return plan

    # Standard: charge only what can be paid in full by next due date
    if due is None:
        warnings.append("Missing payment due day — cannot prove interest-free path")
        safe = ZERO
        path = "unknown due date"
        risk = "blocked"
    else:
        net_before_due = _scheduled_net(schedule, as_of, due, mode)
        capacity = max(ZERO, cash_for_card + net_before_due - balance)
        # Actually: existing statement balance must be paid; new charges on current cycle
        # Safe new charge ≈ cash available to pay statement+new by due, minus current balance
        capacity = max(ZERO, cash_for_card + net_before_due - balance)
        safe = min(available, capacity)
        path = f"Pay statement in full by {due.isoformat()} (no APR)"
        risk = "statement_payoff" if safe > ZERO else "blocked"
        if safe <= ZERO:
            warnings.append(
                f"Cannot prove full payoff by {due.isoformat()} for additional charges"
            )

    return CardPlan(
        account_id=card.id,
        name=card.name,
        safe_to_charge=safe.quantize(Decimal("0.01")),
        payoff_path=path,
        risk=risk,
        utilization_pct=util.quantize(Decimal("0.1")),
        warnings=warnings,
        rewards_rank_eligible=safe > ZERO,
    )


def _scheduled_net(
    schedule: list[ScheduledView],
    start: date,
    end: date,
    mode: str,
) -> Decimal:
    total = ZERO
    for ev in schedule:
        if not (start <= ev.on_date <= end):
            continue
        if ev.amount < 0:
            total += _d(ev.amount)
        else:
            w = _certainty_weight(ev.certainty, mode)
            total += _d(ev.amount) * w
    return total


def compute_ifpp(
    *,
    as_of: date,
    mode: str,
    safety_buffer: Decimal,
    cash_accounts: list[CashAccountView],
    cards: list[CardView],
    schedule: list[ScheduledView],
    horizon_days: int = 45,
    soft_util_pct: int = 10,
    hard_util_pct: int = 30,
    never_negative_scope: str = "checking",
    tax_vault: Decimal = ZERO,
) -> IfppResult:
    mode = mode if mode in ("conservative", "expected") else "conservative"
    cash_spendable, red_day, warnings = compute_cash_spendable(
        cash_accounts,
        schedule,
        as_of=as_of,
        mode=mode,
        safety_buffer=safety_buffer,
        horizon_days=horizon_days,
        never_negative_scope=never_negative_scope,
        tax_vault=tax_vault,
    )

    card_plans: list[CardPlan] = []
    # Rank rewards only among safe cards later
    for card in cards:
        plan = card_safe_to_charge(
            card,
            as_of=as_of,
            cash_available_for_payoff=cash_spendable,
            schedule=schedule,
            mode=mode,
            soft_util_pct=soft_util_pct,
            hard_util_pct=hard_util_pct,
        )
        card_plans.append(plan)
        warnings.extend([f"{card.name}: {w}" for w in plan.warnings])

    # Best rewards among eligible
    eligible = [c for c in card_plans if c.rewards_rank_eligible]
    if eligible:
        # attach note on top rewards card via details
        pass

    card_float = sum((c.safe_to_charge for c in card_plans), ZERO)
    combined = cash_spendable + card_float

    return IfppResult(
        as_of=as_of,
        mode=mode,
        cash_spendable=cash_spendable.quantize(Decimal("0.01")),
        card_float_interest_free=card_float.quantize(Decimal("0.01")),
        combined_purchasing_power=combined.quantize(Decimal("0.01")),
        cards=card_plans,
        next_red_day=red_day,
        warnings=warnings,
        details={
            "horizon_days": horizon_days,
            "safety_buffer": str(safety_buffer),
            "never_negative_scope": never_negative_scope,
            "tax_vault": str(max(ZERO, _d(tax_vault))),
            "cash_accounts": len([a for a in cash_accounts if a.is_cash_for_ifpp]),
            "zero_pct_float_ok": True,
        },
    )


def recommend_card_for_purchase(
    amount: Decimal,
    card_plans: list[CardPlan],
    cards: list[CardView],
) -> CardPlan | None:
    """Pick safest card that can take `amount`, then highest rewards_score."""
    amount = _d(amount)
    by_id = {c.id: c for c in cards}
    candidates = [p for p in card_plans if p.safe_to_charge >= amount]
    if not candidates:
        return None

    def sort_key(p: CardPlan):
        rewards = by_id[p.account_id].rewards_score if p.account_id in by_id else 0
        return (-rewards, -p.safe_to_charge)

    return sorted(candidates, key=sort_key)[0]
