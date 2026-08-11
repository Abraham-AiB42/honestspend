"""Void / reverse a transaction without hard-deleting history."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, Transaction

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _credit_ids_for_void_recompute(session: Session, row: Transaction) -> set[int]:
    """Credit accounts whose Card payment schedule must refresh after this void.

    Includes the voided row's account when credit, and a transfer-pair credit
    account when voiding the cash leg of a mark-paid card payment.
    """
    ids: set[int] = set()
    acct = session.get(Account, row.account_id) if row.account_id else None
    if acct is not None and (acct.kind or "") == "credit" and acct.id is not None:
        ids.add(int(acct.id))
    pair_id = row.transfer_pair_id
    if pair_id:
        pair = session.get(Transaction, pair_id)
        if pair is not None and pair.account_id:
            pacct = session.get(Account, pair.account_id)
            if pacct is not None and (pacct.kind or "") == "credit" and pacct.id is not None:
                ids.add(int(pacct.id))
    return ids


def void_transaction(
    session: Session,
    txn_id: int,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Reverse balance impact and mark status=void.

    Idempotent if already void. Does not remove the row.

    Card payment transfer pairs (mark-paid cash + credit legs): after reverse,
    recompute each related credit Card payment · schedule **once** so next_date
    and amount stay consistent — not double-stepped from mid-flight recompute.
    """
    row = session.get(Transaction, txn_id)
    if not row:
        raise ValueError("Transaction not found")
    if (row.status or "").lower() == "void":
        return {
            "ok": True,
            "transaction_id": row.id,
            "status": "void",
            "already_void": True,
        }

    acct = session.get(Account, row.account_id)
    amt = _d(row.amount)
    credit_ids = _credit_ids_for_void_recompute(session, row)

    if acct and (row.status or "cleared").lower() != "void":
        from financial_os.services.account_balance import reverse_amount_on_account

        # Defer credit recompute until after status=void so schedule projection
        # sees the voided payment and runs exactly once for this void.
        reverse_amount_on_account(
            acct,
            amt,
            session=session,
            recompute=False,
        )

    row.status = "void"
    note = (row.memo or "").strip()
    stamp = datetime.now().isoformat(timespec="seconds")
    void_note = f"[voided {stamp}" + (f": {reason}" if reason else "") + "]"
    row.memo = f"{note} {void_note}".strip() if note else void_note
    session.flush()

    recomputed: list[int] = []
    if credit_ids:
        from financial_os.services.autopay import recompute_card_payment_schedule

        for cid in sorted(credit_ids):
            recompute_card_payment_schedule(session, cid)
            recomputed.append(cid)
        session.flush()

    out: dict[str, Any] = {
        "ok": True,
        "transaction_id": row.id,
        "status": "void",
        "already_void": False,
        "account_id": row.account_id,
        "reversed_amount": str(amt),
        "account_balance": str(_d(acct.current_balance)) if acct else None,
    }
    if recomputed:
        out["recomputed_card_account_ids"] = recomputed
    return out
