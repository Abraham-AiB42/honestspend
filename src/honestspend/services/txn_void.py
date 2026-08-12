"""Void / reverse a transaction without hard-deleting history."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Transaction

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


def _mark_leg_void(row: Transaction, *, reason: str | None, stamp: str) -> None:
    row.status = "void"
    note = (row.memo or "").strip()
    void_note = f"[voided {stamp}" + (f": {reason}" if reason else "") + "]"
    row.memo = f"{note} {void_note}".strip() if note else void_note


def void_transaction(
    session: Session,
    txn_id: int,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Reverse balance impact and mark status=void.

    Idempotent if already void. Does not remove the row.

    Transfer pairs (e.g. mark-paid cash + credit legs): voiding either leg also
    voids the paired txn (skip if already void) so half-void pairs do not linger.
    Reverse both balances with recompute deferred, then recompute each related
    credit Card payment · schedule **once**.
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

    from honestspend.services.account_balance import reverse_amount_on_account

    # Primary + paired leg (if any and not already void). No recursion: we void
    # the pair in this pass only, never re-enter void_transaction for the pair.
    legs: list[Transaction] = [row]
    pair_row: Transaction | None = None
    pair_id = row.transfer_pair_id
    if pair_id:
        pair_row = session.get(Transaction, pair_id)
        if pair_row is not None and (pair_row.status or "").lower() != "void":
            legs.append(pair_row)

    credit_ids: set[int] = set()
    for leg in legs:
        credit_ids |= _credit_ids_for_void_recompute(session, leg)

    stamp = datetime.now().isoformat(timespec="seconds")
    primary_amt = _d(row.amount)
    primary_acct = session.get(Account, row.account_id)

    for leg in legs:
        if (leg.status or "").lower() == "void":
            continue
        acct = session.get(Account, leg.account_id)
        amt = _d(leg.amount)
        if acct is not None:
            # Defer credit recompute until after both legs are status=void so
            # schedule projection sees the full reverse and runs once.
            reverse_amount_on_account(
                acct,
                amt,
                session=session,
                recompute=False,
            )
        _mark_leg_void(leg, reason=reason, stamp=stamp)

    session.flush()

    recomputed: list[int] = []
    if credit_ids:
        from honestspend.services.autopay import recompute_card_payment_schedule

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
        "reversed_amount": str(primary_amt),
        "account_balance": (
            str(_d(primary_acct.current_balance)) if primary_acct else None
        ),
    }
    if pair_row is not None and pair_row.id is not None:
        out["paired_transaction_id"] = pair_row.id
        out["paired_voided"] = (pair_row.status or "").lower() == "void"
    if recomputed:
        out["recomputed_card_account_ids"] = recomputed
    return out
