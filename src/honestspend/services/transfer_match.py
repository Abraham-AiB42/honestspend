"""Suggest transfer pairs across accounts (same abs amount, opposite signs)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Category, Transaction

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def find_transfer_candidates(
    session: Session,
    *,
    days: int = 7,
    as_of: date | None = None,
    profile_id: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Find uncleared transfer pairs: opposite signs, same abs amount, within window."""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=max(1, days))
    q = session.query(Transaction).filter(
        Transaction.txn_date >= start,
        Transaction.txn_date <= as_of,
        Transaction.is_transfer.is_(False),
        Transaction.status != "void",
        Transaction.transfer_pair_id.is_(None),
    )
    if profile_id is not None:
        # still allow cross-profile pairs if both sides present; filter lightly
        pass
    rows = q.order_by(Transaction.txn_date.desc(), Transaction.id.desc()).limit(800).all()

    accts = {a.id: a for a in session.query(Account).all()}
    candidates: list[dict[str, Any]] = []
    used: set[int] = set()

    # Index by rounded abs amount
    by_amt: dict[str, list[Transaction]] = {}
    for t in rows:
        if t.id in used:
            continue
        key = str(abs(_d(t.amount)).quantize(Decimal("0.01")))
        by_amt.setdefault(key, []).append(t)

    for key, group in by_amt.items():
        outs = [t for t in group if _d(t.amount) < ZERO]
        ins = [t for t in group if _d(t.amount) > ZERO]
        for o in outs:
            if o.id in used:
                continue
            for i in ins:
                if i.id in used:
                    continue
                if o.account_id == i.account_id:
                    continue
                # date proximity
                if abs((o.txn_date - i.txn_date).days) > days:
                    continue
                if profile_id is not None and o.profile_id != profile_id and i.profile_id != profile_id:
                    continue
                used.add(o.id)
                used.add(i.id)
                ao = accts.get(o.account_id)
                ai = accts.get(i.account_id)
                candidates.append(
                    {
                        "amount": key,
                        "out_txn_id": o.id,
                        "in_txn_id": i.id,
                        "out_date": o.txn_date.isoformat(),
                        "in_date": i.txn_date.isoformat(),
                        "out_account": ao.nickname if ao else str(o.account_id),
                        "in_account": ai.nickname if ai else str(i.account_id),
                        "out_payee": o.payee or "",
                        "in_payee": i.payee or "",
                        "out_profile_id": o.profile_id,
                        "in_profile_id": i.profile_id,
                        "days_apart": abs((o.txn_date - i.txn_date).days),
                    }
                )
                if len(candidates) >= limit:
                    return {
                        "as_of": as_of.isoformat(),
                        "days": days,
                        "count": len(candidates),
                        "candidates": candidates,
                    }
                break

    return {
        "as_of": as_of.isoformat(),
        "days": days,
        "count": len(candidates),
        "candidates": candidates,
        "hint": "Confirm pairs to mark as transfers (excluded from income/expense).",
    }


def confirm_transfer_pair(
    session: Session,
    *,
    out_txn_id: int,
    in_txn_id: int,
) -> dict[str, Any]:
    out = session.get(Transaction, out_txn_id)
    inn = session.get(Transaction, in_txn_id)
    if not out or not inn:
        raise ValueError("Transaction not found")
    if out.account_id == inn.account_id:
        raise ValueError("Accounts must differ")
    if _d(out.amount) >= ZERO or _d(inn.amount) <= ZERO:
        raise ValueError("Expected outflow + inflow pair")
    if abs(_d(out.amount)) != abs(_d(inn.amount)):
        raise ValueError("Amounts must match")

    transfer_cat = session.query(Category).filter(Category.code == "SYS_TRANSFER").first()
    out.is_transfer = True
    inn.is_transfer = True
    out.transfer_pair_id = inn.id
    inn.transfer_pair_id = out.id
    for leg in (out, inn):
        if leg.memo and "[skip-auto-link]" in leg.memo:
            leg.memo = leg.memo.replace("[skip-auto-link]", "").strip() or None
    if transfer_cat:
        out.category_id = transfer_cat.id
        inn.category_id = transfer_cat.id
    session.flush()
    return {
        "ok": True,
        "out_txn_id": out.id,
        "in_txn_id": inn.id,
        "amount": str(abs(_d(out.amount))),
        "is_transfer": True,
    }
