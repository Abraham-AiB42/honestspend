"""Entity year P&L from the ledger (not day-grid / IFPP).

Buckets use existing category_id + budget_group / tax_form and honest name
hints (payroll package · net / employer tax). No invented carrier lines.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Category, Profile, Transaction

ZERO = Decimal("0")
Q = Decimal("0.01")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal) -> str:
    return str(_d(v).quantize(Q))


def _text_blob(*parts: str | None) -> str:
    return " ".join(p for p in parts if p).lower()


def _classify(
    amount: Decimal,
    cat: Category | None,
    payee: str | None,
    memo: str | None,
) -> str:
    """Return bucket: income | payroll | taxes | owner_draws | expenses | skip."""
    blob = _text_blob(
        payee,
        memo,
        cat.display_name if cat else None,
        cat.code if cat else None,
        cat.budget_group if cat else None,
    )
    bg = (cat.budget_group or "").lower() if cat else ""
    tax_form = (cat.tax_form or "").lower() if cat else ""
    code = (cat.code or "").upper() if cat else ""

    # Transfers / intermix never hit P&L
    if cat and (tax_form == "transfer" or bg in ("transfer", "intermix")):
        return "skip"
    if cat and code in ("SYS_TRANSFER", "SYS_CC_PAYMENT", "SYS_REIMBURSEMENT"):
        return "skip"

    # Owner draw / equity (distributions, contributions) — not expense/income
    if bg == "owner" or tax_form == "equity":
        return "owner_draws"
    if "owner draw" in blob or "owner_draw" in blob:
        return "owner_draws"
    if "distribution" in blob and amount < ZERO:
        return "owner_draws"

    # Taxes before payroll so "… payroll · employer tax" is not swallowed by payroll
    if bg.startswith("tax"):
        return "taxes"
    if "employer tax" in blob or "tax vault" in blob:
        return "taxes"
    base_code = code.split("__")[0] if code else ""
    if base_code in ("BIZ_TAXES_LICENSES", "PER_EST_TAX") or "taxes and licenses" in blob:
        return "taxes"

    # Payroll (W-2 / salaries / package · net) — after employer-tax leg
    if bg == "payroll":
        return "payroll"
    if "payroll" in blob or "package net" in blob:
        return "payroll"
    if " · net" in blob or "· net" in blob:
        # Payroll package net leg: "{name} · net"
        return "payroll"

    # Income categories or positive non-transfer credits
    if bg == "income":
        return "income"
    if amount > ZERO:
        return "income"

    # Remaining outflows are expenses
    if amount < ZERO:
        return "expenses"

    return "skip"


def build_entity_pnl(
    session: Session,
    profile_id: int,
    year: int,
) -> dict[str, Any]:
    """Year P&L for one entity from ledger transactions."""
    profile = session.get(Profile, profile_id)
    if not profile:
        raise ValueError("Profile not found")

    start = date(year, 1, 1)
    end = date(year, 12, 31)

    txns = (
        session.query(Transaction)
        .filter(
            Transaction.profile_id == profile_id,
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
            Transaction.is_transfer.is_(False),
            Transaction.status != "void",
        )
        .order_by(Transaction.txn_date, Transaction.id)
        .all()
    )

    cats = {c.id: c for c in session.query(Category).all()}

    buckets = {
        "income": ZERO,
        "payroll": ZERO,
        "taxes": ZERO,
        "owner_draws": ZERO,
        "expenses": ZERO,
    }
    by_cat_amt: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_cat_n: dict[str, int] = defaultdict(int)
    counts = {k: 0 for k in buckets}

    for t in txns:
        amt = _d(t.amount)
        cat = cats.get(t.category_id) if t.category_id else None
        bucket = _classify(amt, cat, t.payee, t.memo)
        if bucket == "skip" or bucket not in buckets:
            continue
        buckets[bucket] += amt
        counts[bucket] += 1
        label = cat.display_name if cat else "Uncategorized"
        by_cat_amt[label] += amt
        by_cat_n[label] += 1

    net = (
        buckets["income"]
        + buckets["payroll"]
        + buckets["taxes"]
        + buckets["owner_draws"]
        + buckets["expenses"]
    )

    top = sorted(by_cat_amt.items(), key=lambda kv: abs(kv[1]), reverse=True)[:12]
    by_category = [
        {"category": name, "amount": _q(amt), "count": by_cat_n[name]}
        for name, amt in top
    ]

    return {
        "profile_id": profile_id,
        "display_name": profile.display_name,
        "entity_type": profile.entity_type,
        "year": year,
        "income": _q(buckets["income"]),
        "payroll": _q(buckets["payroll"]),
        "taxes": _q(buckets["taxes"]),
        "owner_draws": _q(buckets["owner_draws"]),
        "expenses": _q(buckets["expenses"]),
        "net": _q(net),
        "by_category": by_category,
        "counts": counts,
        "title": f"{profile.display_name} · {year} P&L",
        "principle": "Ledger year view — income, payroll, taxes, draws, expenses. Not day-grid.",
    }
