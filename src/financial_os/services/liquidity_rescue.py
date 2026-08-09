"""Avoid-negative coach — ranked options with estimated costs."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, ScheduledItem
from financial_os.services.ifpp_service import run_ifpp

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def build_rescue_plan(
    session: Session,
    *,
    shortfall: Decimal | None = None,
    amount: Decimal | None = None,
    account_id: int | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Ranked ways to avoid going negative / cover a shortfall.

    shortfall: positive dollars needed. If omitted, derived from amount vs spendable
    or from already-negative checking.
    """
    as_of = as_of or date.today()
    ifpp = run_ifpp(session, as_of=as_of, profile_id=profile_id, scope=scope)
    need = _d(shortfall) if shortfall is not None else ZERO

    if need <= ZERO and amount is not None:
        need = max(ZERO, _d(amount) - ifpp.cash_spendable)

    if need <= ZERO and ifpp.is_red_now:
        # Cover negative checking in scope
        q = session.query(Account).filter(
            Account.kind == "checking",
            Account.archived_at.is_(None),
        )
        if (ifpp.details or {}).get("ifpp_scope") == "entity" and ifpp.details.get(
            "profile_id"
        ):
            q = q.filter(Account.profile_id == ifpp.details["profile_id"])
        for a in q.all():
            if _d(a.current_balance) < ZERO:
                need = max(need, abs(_d(a.current_balance)))

    if need <= ZERO and amount is not None:
        need = abs(_d(amount))

    options: list[dict[str, Any]] = []
    rank = 1

    # 1. Transfer from savings / HYSA / money market (same entity preferred)
    cash_q = session.query(Account).filter(
        Account.kind.in_(("savings", "cash", "other")),
        Account.archived_at.is_(None),
        Account.current_balance > 0,
    )
    pid = (ifpp.details or {}).get("profile_id")
    if (ifpp.details or {}).get("ifpp_scope") == "entity" and pid:
        cash_q = cash_q.filter(Account.profile_id == pid)
    for a in cash_q.order_by(Account.current_balance.desc()).all():
        bal = _d(a.current_balance)
        take = min(bal, need) if need > ZERO else bal
        if take <= ZERO:
            continue
        apy = _d(a.apy) if a.apy is not None else ZERO
        # 30-day opportunity cost of pulling cash out of yield
        opp_30 = (take * apy / Decimal("12")).quantize(Decimal("0.01"))
        options.append(
            {
                "rank": rank,
                "action": "transfer_in",
                "title": f"Transfer ${take} from {a.nickname}",
                "amount": str(take),
                "account_id": a.id,
                "safe": True,
                "cost_estimate": str(opp_30),
                "cost_unit": "est_yield_forgone_30d",
                "reason": (
                    f"Move cash from {a.kind} into checking. "
                    f"APY {apy} → ~${opp_30} yield forgone over ~30 days."
                ),
                "side_effects": ["reduces yield balance", "books: transfer not expense"],
                "priority": "fiscal",
            }
        )
        rank += 1

    # 2. Defer discretionary scheduled outflows
    sched_q = session.query(ScheduledItem).filter(
        ScheduledItem.active.is_(True),
        ScheduledItem.amount < 0,
    )
    if (ifpp.details or {}).get("ifpp_scope") == "entity" and pid:
        sched_q = sched_q.filter(ScheduledItem.profile_id == pid)
    deferrable = []
    for s in sched_q.all():
        notes = (s.notes or "").lower()
        name = (s.name or "").lower()
        # Never auto-suggest deferring rent/housing/utilities labeled fixed essentials
        blocked = any(
            k in name for k in ("rent", "mortgage", "housing", "electric", "gas bill", "water")
        )
        if blocked and "deferrable" not in notes:
            continue
        if s.certainty == "fixed" and "deferrable" not in notes and not any(
            k in name for k in ("subscription", "streaming", "gym", "entertainment", "discretionary")
        ):
            # only soft-suggest non-essential-looking names
            if not any(k in name for k in ("sub", "netflix", "spotify", "hobby", "gift")):
                continue
        amt = abs(_d(s.amount))
        deferrable.append((s, amt))
    deferrable.sort(key=lambda x: -x[1])
    freed = ZERO
    for s, amt in deferrable[:5]:
        freed += amt
        options.append(
            {
                "rank": rank,
                "action": "defer_bill",
                "title": f"Defer '{s.name}' (${amt})",
                "amount": str(amt),
                "scheduled_id": s.id,
                "safe": "deferrable" in (s.notes or "").lower(),
                "cost_estimate": "0",
                "cost_unit": "late_fee_risk_unknown",
                "reason": (
                    f"Skip/delay this outflow in the IFPP horizon. "
                    f"Only safe if vendor allows; mark notes with 'deferrable' to prefer."
                ),
                "side_effects": ["may incur late fees", "credit risk if it's a loan/card min"],
                "priority": "fiscal",
            }
        )
        rank += 1

    # 3. 0% / interest-free card float (from IFPP plans)
    for c in ifpp.cards:
        if c.safe_to_charge <= ZERO:
            continue
        take = min(c.safe_to_charge, need) if need > ZERO else c.safe_to_charge
        options.append(
            {
                "rank": rank,
                "action": "card_float_interest_free",
                "title": f"Charge ${take} on {c.name} (interest-free path)",
                "amount": str(take),
                "account_id": c.account_id,
                "safe": True,
                "cost_estimate": "0",
                "cost_unit": "interest",
                "reason": c.payoff_path,
                "side_effects": c.warnings or [],
                "priority": "fiscal",
            }
        )
        rank += 1

    # 4. Interest-bearing card (show cost) — last resort fiscal
    cards = (
        session.query(Account)
        .filter(Account.kind == "credit", Account.archived_at.is_(None))
        .all()
    )
    if (ifpp.details or {}).get("ifpp_scope") == "entity" and pid:
        cards = [a for a in cards if a.profile_id == pid]
    for a in cards:
        limit = _d(a.credit_limit)
        bal = _d(a.current_balance)
        avail = _d(a.available_credit) if a.available_credit is not None else max(ZERO, limit - bal)
        if avail <= ZERO:
            continue
        # Skip if already listed as interest-free with capacity
        if any(
            o.get("account_id") == a.id and o["action"] == "card_float_interest_free"
            for o in options
        ):
            continue
        take = min(avail, need) if need > ZERO else min(avail, Decimal("500"))
        if take <= ZERO:
            continue
        apr = _d(a.apr)
        # Rough 1-month interest if revolved
        interest_1m = (take * apr / Decimal("12")).quantize(Decimal("0.01"))
        options.append(
            {
                "rank": rank,
                "action": "card_revolve_apr",
                "title": f"Charge ${take} on {a.nickname} (APR risk)",
                "amount": str(take),
                "account_id": a.id,
                "safe": False,
                "cost_estimate": str(interest_1m),
                "cost_unit": "est_interest_1mo_if_revolved",
                "reason": (
                    f"No proven interest-free path. If you revolve at APR {apr}, "
                    f"~${interest_1m}/mo on ${take}. Prefer transfer or defer first."
                ),
                "side_effects": ["utilization up", "possible APR interest", "credit score pressure"],
                "priority": "last_resort",
            }
        )
        rank += 1

    # 5. Investment / brokerage advisory (not a brokerage product)
    inv = session.query(Account).filter(
        Account.kind == "investment",
        Account.archived_at.is_(None),
        Account.current_balance > 0,
    )
    if (ifpp.details or {}).get("ifpp_scope") == "entity" and pid:
        inv = inv.filter(Account.profile_id == pid)
    for a in inv.all():
        bal = _d(a.current_balance)
        take = min(bal, need) if need > ZERO else min(bal, Decimal("1000"))
        if take <= ZERO:
            continue
        options.append(
            {
                "rank": rank,
                "action": "sell_investment_advisory",
                "title": f"Sell ~${take} from {a.nickname} (advisory)",
                "amount": str(take),
                "account_id": a.id,
                "safe": False,
                "cost_estimate": "unknown",
                "cost_unit": "tax_and_opportunity",
                "reason": (
                    "Liquidate investment holdings only if necessary. "
                    "May trigger taxes and miss future gains. Not a brokerage trade — record manually."
                ),
                "side_effects": ["possible capital gains tax", "market timing risk"],
                "priority": "last_resort",
                "disclaimer": "Not investment advice.",
            }
        )
        rank += 1

    # 6. Intermix inject from other entity cash (group view)
    other = session.query(Account).filter(
        Account.kind == "checking",
        Account.archived_at.is_(None),
        Account.current_balance > 0,
    )
    if pid:
        other = other.filter(Account.profile_id != pid)
    for a in other.order_by(Account.current_balance.desc()).limit(3).all():
        take = min(_d(a.current_balance), need) if need > ZERO else _d(a.current_balance)
        if take <= ZERO:
            continue
        options.append(
            {
                "rank": rank,
                "action": "intermix_inject",
                "title": f"Capital inject ${take} from {a.nickname}",
                "amount": str(take),
                "account_id": a.id,
                "safe": True,
                "cost_estimate": "0",
                "cost_unit": "books_only",
                "reason": "Move cash across entities with correct intermix books (not income).",
                "side_effects": ["reduces other entity Spendable"],
                "priority": "fiscal",
            }
        )
        rank += 1

    # Sort: safe fiscal first, then by cost
    def sort_key(o: dict) -> tuple:
        safe = 0 if o.get("safe") else 1
        last = 0 if o.get("priority") != "last_resort" else 1
        try:
            cost = float(o.get("cost_estimate") or 0)
        except (TypeError, ValueError):
            cost = 9999.0
        return (safe, last, cost, o.get("rank", 99))

    options.sort(key=sort_key)
    for i, o in enumerate(options, start=1):
        o["rank"] = i

    return {
        "as_of": as_of.isoformat(),
        "shortfall": str(need.quantize(Decimal("0.01"))),
        "cash_spendable": str(ifpp.cash_spendable),
        "is_red_now": ifpp.is_red_now,
        "next_red_day": ifpp.next_red_day.isoformat() if ifpp.next_red_day else None,
        "ifpp_scope": (ifpp.details or {}).get("ifpp_scope"),
        "profile_id": (ifpp.details or {}).get("profile_id"),
        "options": options,
        "message": (
            f"Need ~${need} to stay non-negative / cover the gap. "
            f"{len(options)} option(s) ranked by fiscal soundness then cost."
            if need > ZERO
            else "No shortfall detected; options still listed for planning."
        ),
        "principle": "Fiscal #1 — prefer transfers and deferrals over revolving APR.",
    }
