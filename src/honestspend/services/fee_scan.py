"""Detect dumb fees in the ledger (keywords + category codes)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Category, Transaction

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
    profile_id: int | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    start = as_of - timedelta(days=max(1, days))
    cats = {c.id: c for c in session.query(Category).all()}
    q = session.query(Transaction).filter(
        Transaction.txn_date >= start,
        Transaction.txn_date <= as_of,
        Transaction.is_transfer.is_(False),
    )
    if profile_id is not None:
        q = q.filter(Transaction.profile_id == profile_id)
    # Hide dismissed fee candidates (fee_status column when present)
    if hasattr(Transaction, "fee_status"):
        q = q.filter(
            (Transaction.fee_status.is_(None)) | (Transaction.fee_status != "dismissed")
        )
    rows = q.order_by(Transaction.txn_date.desc()).limit(500).all()
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
        "profile_id": profile_id,
    }


def confirm_fee(
    session: Session,
    *,
    transaction_id: int,
    action: str,
    category_id: int | None = None,
) -> dict[str, Any]:
    """mark_fee | dismiss | recategorize."""
    txn = session.get(Transaction, transaction_id)
    if not txn:
        raise ValueError("Transaction not found")
    action = (action or "").lower().strip()
    if action not in ("mark_fee", "dismiss", "recategorize"):
        raise ValueError("action must be mark_fee, dismiss, or recategorize")

    if action == "dismiss":
        if hasattr(txn, "fee_status"):
            txn.fee_status = "dismissed"
        session.flush()
        return {"ok": True, "transaction_id": txn.id, "action": "dismiss", "fee_status": "dismissed"}

    if action == "recategorize":
        if not category_id:
            raise ValueError("category_id required for recategorize")
        cat = session.get(Category, category_id)
        if not cat:
            raise ValueError("Category not found")
        txn.category_id = category_id
        if hasattr(txn, "fee_status"):
            txn.fee_status = "recategorized"
        session.flush()
        return {
            "ok": True,
            "transaction_id": txn.id,
            "action": "recategorize",
            "category_id": category_id,
        }

    # mark_fee — prefer FEE_OTHER / SYS_FEE category
    fee_cat = (
        session.query(Category)
        .filter(Category.code.in_(("FEE_OTHER", "SYS_FEE", "FEE_LATE")))
        .order_by(Category.id)
        .first()
    )
    if category_id:
        fee_cat = session.get(Category, category_id) or fee_cat
    if fee_cat:
        txn.category_id = fee_cat.id
    if hasattr(txn, "fee_status"):
        txn.fee_status = "confirmed_fee"
    session.flush()
    return {
        "ok": True,
        "transaction_id": txn.id,
        "action": "mark_fee",
        "category_id": txn.category_id,
        "fee_status": getattr(txn, "fee_status", None),
    }


def fee_summary(
    session: Session,
    *,
    days: int = 365,
    profile_id: int | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    scan = scan_fees(session, days=days, limit=200, as_of=as_of, profile_id=profile_id)
    total = _d(scan["total_abs"])
    # Suggest buffer at least 2x monthly fee rate or $500
    monthly = (total / Decimal(max(1, days)) * Decimal("30")).quantize(Decimal("0.01"))
    suggested_buffer = max(Decimal("500"), monthly * Decimal("2"))
    return {
        **scan,
        "period_days": days,
        "est_monthly_fee_drag": str(monthly),
        "suggested_safety_buffer": str(suggested_buffer),
        "message": (
            f"~${total} fee-like outflows in {days}d (~${monthly}/mo). "
            f"Consider safety buffer ≥ ${suggested_buffer}."
        ),
    }
