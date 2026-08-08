"""Detect dumb fees in the ledger (keywords + category codes)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Category, Transaction

# Payee/memo heuristics (case-insensitive contains)
FEE_KEYWORDS = (
    "late fee",
    "overdraft",
    "nsf ",
    "nsf fee",
    "insufficient fund",
    "interest charge",
    "finance charge",
    "annual fee",
    "membership fee",
    "foreign transaction",
    "atm fee",
    "service fee",
    "maintenance fee",
    "returned item",
    "penalty",
    "over limit",
    "overlimit",
)

FEE_CATEGORY_CODES = (
    "FEE_LATE",
    "FEE_OD",
    "FEE_INTEREST",
    "FEE_ANNUAL",
    "FEE_OTHER",
    "SYS_FEE",
)


def _d(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _match_reason(payee: str, memo: str, cat_code: str | None) -> str | None:
    blob = f"{payee} {memo}".lower()
    if cat_code and (cat_code in FEE_CATEGORY_CODES or cat_code.startswith("FEE_")):
        return f"category {cat_code}"
    for kw in FEE_KEYWORDS:
        if kw in blob:
            return f"keyword:{kw.strip()}"
    return None


def scan_fees(
    session: Session,
    *,
    days: int = 90,
    limit: int = 50,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    start = as_of - timedelta(days=max(1, days))
    cats = {c.id: c for c in session.query(Category).all()}
    rows = (
        session.query(Transaction)
        .filter(
            Transaction.txn_date >= start,
            Transaction.txn_date <= as_of,
            Transaction.is_transfer.is_(False),
        )
        .order_by(Transaction.txn_date.desc())
        .limit(500)
        .all()
    )
    hits: list[dict[str, Any]] = []
    total = Decimal("0")
    for t in rows:
        # Fees are usually outflows
        if _d(t.amount) >= 0:
            continue
        cat = cats.get(t.category_id) if t.category_id else None
        code = cat.code if cat else None
        reason = _match_reason(t.payee or "", t.memo or "", code)
        if not reason:
            continue
        amt = abs(_d(t.amount))
        total += amt
        hits.append(
            {
                "transaction_id": t.id,
                "txn_date": t.txn_date.isoformat(),
                "payee": t.payee or "",
                "amount": str(t.amount),
                "abs_amount": str(amt),
                "reason": reason,
                "category": cat.display_name if cat else None,
                "profile_id": t.profile_id,
                "account_id": t.account_id,
            }
        )
        if len(hits) >= limit:
            break

    return {
        "as_of": as_of.isoformat(),
        "days": days,
        "count": len(hits),
        "total_abs": str(total.quantize(Decimal("0.01"))),
        "candidates": hits,
        "principle": "Fees are stupid — confirm, categorize, and stop the recurring ones.",
    }
