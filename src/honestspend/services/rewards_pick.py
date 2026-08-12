"""Pick a credit card for a spend category via smart_charge (everyone product).

Rates are percent points on the card: rewards_rates_json e.g.
  {"gas": 5, "groceries": 3, "restaurants": 2, "general": 1, "amazon": 5}

Falls back to rewards_program heuristic, then general.
Order comes from rank_charge: float (interest-free days) first, then rewards rate.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account

ZERO = Decimal("0")
CENT = Decimal("0.01")

# Canonical category keys (UI can map display labels → keys)
CATEGORY_KEYS = frozenset(
    {
        "general",
        "gas",
        "groceries",
        "restaurants",
        "travel",
        "amazon",
        "online",
        "drugstore",
        "home_improvement",
        "entertainment",
        "utilities",
        "other",
    }
)

# Heuristic keywords in rewards_program / nickname when no JSON rates
PROGRAM_HINTS: dict[str, list[str]] = {
    "gas": ["gas", "fuel", "shell", "exxon", "chevron"],
    "groceries": ["grocery", "groceries", "kroger", "safeway", "whole foods"],
    "restaurants": ["dining", "restaurant", "uber eats", "doordash"],
    "amazon": ["amazon", "prime"],
    "travel": ["travel", "airline", "hotel", "chase sapphire", "venture"],
    "home_improvement": ["home depot", "lowe", "hd "],
}


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def normalize_category(raw: str | None) -> str:
    k = (raw or "general").strip().lower().replace(" ", "_").replace("-", "_")
    if k in CATEGORY_KEYS:
        return k
    # aliases
    aliases = {
        "food": "groceries",
        "grocery": "groceries",
        "dining": "restaurants",
        "restaurant": "restaurants",
        "fuel": "gas",
        "petrol": "gas",
        "home": "home_improvement",
        "depot": "home_improvement",
        "shopping": "general",
    }
    return aliases.get(k, "general" if k not in CATEGORY_KEYS else k)


def parse_rewards_rates(raw: str | None) -> dict[str, Decimal]:
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    out: dict[str, Decimal] = {}
    if not isinstance(data, dict):
        return out
    for k, v in data.items():
        key = normalize_category(str(k))
        try:
            out[key] = _d(v)
        except Exception:
            continue
    return out


def rates_for_account(account: Account) -> dict[str, Decimal]:
    rates = parse_rewards_rates(getattr(account, "rewards_rates_json", None))
    if rates:
        return rates
    # Heuristic from program name
    blob = f"{account.rewards_program or ''} {account.nickname or ''}".lower()
    out: dict[str, Decimal] = {"general": Decimal("1") if account.rewards_program else ZERO}
    for cat, hints in PROGRAM_HINTS.items():
        if any(h in blob for h in hints):
            out[cat] = Decimal("3")  # modest default boost
    return out


def rate_for_category(account: Account, category: str) -> Decimal:
    rates = rates_for_account(account)
    cat = normalize_category(category)
    if cat in rates:
        return rates[cat]
    return rates.get("general", ZERO)


def set_rewards_rates(
    session: Session,
    account_id: int,
    rates: dict[str, Any],
) -> Account:
    acct = session.get(Account, account_id)
    if not acct:
        raise ValueError(f"Account {account_id} not found")
    if (acct.kind or "") != "credit":
        raise ValueError("Rewards rates apply to credit accounts only")
    clean: dict[str, float] = {}
    for k, v in (rates or {}).items():
        key = normalize_category(str(k))
        clean[key] = float(_d(v))
    acct.rewards_rates_json = json.dumps(clean) if clean else None
    session.flush()
    return acct


def pick_card_for_category(
    session: Session,
    *,
    category: str,
    amount: Decimal | Any | None = None,
    profile_id: int | None = None,
    as_of: date | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Build a dummy amount (amount or 1.00), call rank_charge with proposed=first card.
    Return cards in ranker order. Hint: rewards never beat a tighter pay date."""
    from honestspend.services.smart_charge import rank_charge

    cat = normalize_category(category)
    as_of = as_of or date.today()
    # rank_charge needs a positive amount; default $1 when caller omits amount
    if amount is not None:
        amt = max(CENT, _d(amount))
        amount_out = str(_d(amount).quantize(CENT)) if _d(amount) > ZERO else str(amt)
    else:
        amt = Decimal("1.00")
        amount_out = None

    q = session.query(Account).filter(
        Account.kind == "credit",
        Account.archived_at.is_(None),
    )
    if profile_id is not None:
        q = q.filter(Account.profile_id == profile_id)
    cards = q.order_by(Account.id).all()
    by_id = {a.id: a for a in cards}

    if not cards:
        return {
            "category": cat,
            "amount": amount_out,
            "best": None,
            "cards": [],
            "categories": sorted(CATEGORY_KEYS),
            "hint": (
                "Rewards never beat a tighter pay date. "
                "We rank interest-free float first, then category rewards."
            ),
        }

    proposed_id = cards[0].id
    ranked_res = rank_charge(
        session,
        amount=amt,
        reward_category=cat,
        proposed_account_id=proposed_id,
        as_of=as_of,
        profile_id=profile_id,
        scope=scope,
        promo={"mode": "none"},
    )

    ranked: list[dict[str, Any]] = []
    seen: set[int] = set()
    for opt in ranked_res.get("options") or []:
        if (opt.get("method") or "") != "card":
            continue
        aid = opt.get("account_id")
        if aid is None or aid in seen:
            continue
        a = by_id.get(int(aid)) or session.get(Account, int(aid))
        if a is None or (a.kind or "") != "credit":
            continue
        if profile_id is not None and a.profile_id != profile_id:
            continue
        seen.add(int(aid))
        bal = _d(a.current_balance)
        limit = _d(a.credit_limit) if a.credit_limit is not None else None
        available = (
            _d(a.available_credit)
            if a.available_credit is not None
            else ((limit - bal) if limit is not None else None)
        )
        rate = _d(opt.get("rewards_rate"))
        fits = bool(opt.get("safe"))
        if amt > ZERO and available is not None and available < amt:
            fits = False
        util = None
        if limit and limit > ZERO:
            util = (bal / limit * Decimal("100")).quantize(CENT)
        ranked.append(
            {
                "account_id": a.id,
                "name": a.nickname,
                "category": cat,
                "rate_percent": str(rate.quantize(CENT)),
                "rewards_program": a.rewards_program,
                "available_credit": str(available.quantize(CENT))
                if available is not None
                else None,
                "utilization_pct": str(util) if util is not None else None,
                "fits_amount": fits,
                "balance": str(bal.quantize(CENT)),
                "float_days": int(opt.get("float_days") or 0),
                "safe": bool(opt.get("safe")),
                "reason": opt.get("reason"),
            }
        )

    # Any credit not returned by ranker (edge) appends at end, rate order
    for a in cards:
        if a.id in seen:
            continue
        bal = _d(a.current_balance)
        limit = _d(a.credit_limit) if a.credit_limit is not None else None
        available = (
            _d(a.available_credit)
            if a.available_credit is not None
            else ((limit - bal) if limit is not None else None)
        )
        rate = rate_for_category(a, cat)
        fits = not (amt > ZERO and available is not None and available < amt)
        util = None
        if limit and limit > ZERO:
            util = (bal / limit * Decimal("100")).quantize(CENT)
        ranked.append(
            {
                "account_id": a.id,
                "name": a.nickname,
                "category": cat,
                "rate_percent": str(rate.quantize(CENT)),
                "rewards_program": a.rewards_program,
                "available_credit": str(available.quantize(CENT))
                if available is not None
                else None,
                "utilization_pct": str(util) if util is not None else None,
                "fits_amount": fits,
                "balance": str(bal.quantize(CENT)),
                "float_days": 0,
                "safe": False,
                "reason": None,
            }
        )

    best = ranked[0] if ranked else None

    return {
        "category": cat,
        "amount": amount_out,
        "best": best,
        "cards": ranked,
        "categories": sorted(CATEGORY_KEYS),
        "recommended": ranked_res.get("recommended"),
        "verdict": ranked_res.get("verdict"),
        "why": ranked_res.get("why"),
        "hint": (
            "Rewards never beat a tighter pay date. "
            "We rank interest-free float first, then category rewards — "
            "still honor Safe to spend and never-neg on checking."
        ),
    }
