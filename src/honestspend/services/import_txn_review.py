"""D12: statement is issuer-cycle truth; books own prior transactions.

Never overwrite amount/date/payee/category/Who on an existing books row.
Amount/date mismatch on fingerprint/external_id match → review_txn_mismatch.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Transaction

CENT = Decimal("0.01")


def _q(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def txn_mismatch_reviews(*, existing: Transaction, incoming: dict) -> dict | None:
    """If amount or date differ vs books: return review item; else None.

    Caller must NOT write amount/date/payee/category on existing.
    Identity (fingerprint / external_id) is established by the caller.
    """
    books_amt = _q(existing.amount)
    stmt_amt = _q(incoming.get("amount"))
    books_date = existing.txn_date
    stmt_date = _as_date(incoming.get("txn_date"))

    amt_diff = (
        books_amt is not None
        and stmt_amt is not None
        and books_amt != stmt_amt
    )
    date_diff = (
        books_date is not None
        and stmt_date is not None
        and books_date != stmt_date
    )
    if not amt_diff and not date_diff:
        return None

    return {
        "action": "review_txn_mismatch",
        "txn_id": int(existing.id) if existing.id is not None else None,
        "books_amount": str(books_amt) if books_amt is not None else None,
        "statement_amount": str(stmt_amt) if stmt_amt is not None else None,
        "books_date": books_date.isoformat() if books_date else None,
        "statement_date": stmt_date.isoformat() if stmt_date else None,
        "payee": (existing.payee or incoming.get("payee") or "")[:128] or None,
        "external_id": existing.external_id,
        "label": "Statement amount/date differs from books",
        "detail": (
            f"Books ${books_amt} on {books_date} vs statement ${stmt_amt}"
            f" on {stmt_date}. Books kept; review and fix if needed."
        ),
    }


def find_existing_by_external_id(
    session: Session,
    account_id: int,
    external_id: str | None,
) -> Transaction | None:
    """Look up books row by exact external_id for this account."""
    ext = (external_id or "").strip()
    if not ext:
        return None
    return (
        session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.external_id == ext,
        )
        .first()
    )


def review_step_from_mismatch(m: dict) -> dict[str, str]:
    """Shape a next_steps entry for post-import CTA."""
    return {
        "action": "review_txn_mismatch",
        "label": str(m.get("label") or "Review transaction mismatch"),
        "detail": str(
            m.get("detail")
            or (
                f"Books ${m.get('books_amount')} vs statement ${m.get('statement_amount')}"
            )
        ),
        "txn_id": str(m.get("txn_id") or ""),
        "account_id": "",
    }
