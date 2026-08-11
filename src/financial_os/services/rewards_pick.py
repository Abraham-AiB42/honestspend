"""Pick a credit card for a spend category by rewards rate (everyone product).

Rates are percent points on the card: rewards_rates_json e.g.
  {"gas": 5, "groceries": 3, "restaurants": 2, "general": 1, "amazon": 5}

Falls back to rewards_program heuristic, then general, then any credit with room.
Does not replace Safe to spend / interest-free float — only ranks rewards among cards.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account

ZERO = Decimal("0")

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
) -> dict[str, Any]:
    """Rank credit cards for a category; best = highest rate with room for amount."""
    cat = normalize_category(category)
    amt = max(ZERO, _d(amount)) if amount is not None else ZERO

    q = session.query(Account).filter(
        Account.kind == "credit",
        Account.archived_at.is_(None),
    )
    if profile_id is not None:
        q = q.filter(Account.profile_id == profile_id)
    cards = q.order_by(Account.id).all()

    ranked = []
    for a in cards:
        bal = _d(a.current_balance)
        limit = _d(a.credit_limit) if a.credit_limit is not None else None
        available = (
            _d(a.available_credit)
            if a.available_credit is not None
            else ((limit - bal) if limit is not None else None)
        )
        rate = rate_for_category(a, cat)
        fits = True
        if amt > ZERO and available is not None and available < amt:
            fits = False
        util = None
        if limit and limit > ZERO:
            util = (bal / limit * Decimal("100")).quantize(Decimal("0.01"))
        ranked.append(
            {
                "account_id": a.id,
                "name": a.nickname,
                "category": cat,
                "rate_percent": str(rate.quantize(Decimal("0.01"))),
                "rewards_program": a.rewards_program,
                "available_credit": str(available.quantize(Decimal("0.01")))
                if available is not None
                else None,
                "utilization_pct": str(util) if util is not None else None,
                "fits_amount": fits,
                "balance": str(bal.quantize(Decimal("0.01"))),
            }
        )

    # Sort: fits first, then rate desc, then lower util
    def _key(r: dict[str, Any]) -> tuple:
        fits_n = 0 if r["fits_amount"] else 1
        rate_n = -float(r["rate_percent"] or 0)
        util_n = float(r["utilization_pct"] or 999)
        return (fits_n, rate_n, util_n)

    ranked.sort(key=_key)
    best = ranked[0] if ranked else None

    return {
        "category": cat,
        "amount": str(amt.quantize(Decimal("0.01"))) if amount is not None else None,
        "best": best,
        "cards": ranked,
        "categories": sorted(CATEGORY_KEYS),
        "hint": (
            "Pick the highest rewards rate card that still has room. "
            "Still pay with Safe to spend / interest-free rules in mind — rewards never beat bounced checking."
        ),
    }
