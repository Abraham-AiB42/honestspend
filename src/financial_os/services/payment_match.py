"""Detect cash→card payments that match scheduled bills or card balances."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, ScheduledItem, Transaction

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def find_payment_candidates(
    session: Session,
    *,
    days: int = 14,
    as_of: date | None = None,
    profile_id: int | None = None,
    amount_tolerance: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    """Suggest cash outflows that look like credit-card payments.

    Heuristics:
    - Cash account outflow (negative amount)
    - Same abs amount as a credit card payment/inflow on a credit account within window
      OR matches a scheduled Autopay / card bill amount
    - Not already marked transfer
    """
    as_of = as_of or date.today()
    start = as_of - timedelta(days=max(1, days))

    accts = {
        a.id: a
        for a in session.query(Account).filter(Account.archived_at.is_(None)).all()
    }
    cash_ids = {i for i, a in accts.items() if a.kind in ("checking", "savings", "cash")}
    card_ids = {i for i, a in accts.items() if a.kind == "credit"}

    tq = session.query(Transaction).filter(
        Transaction.txn_date >= start,
        Transaction.txn_date <= as_of,
        Transaction.is_transfer.is_(False),
        Transaction.status != "void",
    )
    if profile_id is not None:
        tq = tq.filter(Transaction.profile_id == profile_id)
    txns = tq.order_by(Transaction.txn_date.desc()).limit(800).all()

    cash_out = [t for t in txns if t.account_id in cash_ids and _d(t.amount) < ZERO]
    # Credit payment often posts as positive (payment reduces owed) depending on convention
    # Our create_transaction: credit charge is negative amount increases owed;
    # so a payment would be positive amount (reduces owed).
    card_pay = [t for t in txns if t.account_id in card_ids and _d(t.amount) > ZERO]

    sched_q = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if profile_id is not None:
        sched_q = sched_q.filter(ScheduledItem.profile_id == profile_id)
    schedules = sched_q.all()

    candidates: list[dict[str, Any]] = []
    used_cash: set[int] = set()
    used_card: set[int] = set()

    for co in cash_out:
        if co.id in used_cash:
            continue
        abs_amt = abs(_d(co.amount))
        # Pair with card payment txn
        for cp in card_pay:
            if cp.id in used_card:
                continue
            if abs(abs_amt - _d(cp.amount)) > amount_tolerance:
                continue
            if abs((co.txn_date - cp.txn_date).days) > days:
                continue
            if profile_id is None and accts[co.account_id].profile_id != accts[cp.account_id].profile_id:
                # allow cross-profile only if same household later; skip different profiles by default
                continue
            used_cash.add(co.id)
            used_card.add(cp.id)
            cash_a = accts[co.account_id]
            card_a = accts[cp.account_id]
            candidates.append(
                {
                    "kind": "cash_to_card_pair",
                    "amount": str(abs_amt),
                    "cash_txn_id": co.id,
                    "card_txn_id": cp.id,
                    "cash_account": cash_a.nickname,
                    "card_account": card_a.nickname,
                    "cash_date": co.txn_date.isoformat(),
                    "card_date": cp.txn_date.isoformat(),
                    "days_apart": abs((co.txn_date - cp.txn_date).days),
                    "suggestion": "Mark as transfer pair (payment) so Spendable doesn't double-count.",
                    "action": "confirm_transfer",
                }
            )
            break
        else:
            # Match scheduled autopay / bill by amount near due
            for s in schedules:
                if s.account_id not in card_ids and s.account_id not in cash_ids:
                    continue
                sam = abs(_d(s.amount))
                if abs(sam - abs_amt) > amount_tolerance:
                    continue
                # name hints
                name = (s.name or "").lower()
                payee = (co.payee or "").lower()
                if not any(
                    k in name or k in payee
                    for k in ("autopay", "payment", "card", "credit", "visa", "amex", "mc ", "mastercard")
                ):
                    if "autopay" not in name and "sink" not in name:
                        continue
                used_cash.add(co.id)
                card_a = accts.get(s.account_id) if s.account_id in card_ids else None
                candidates.append(
                    {
                        "kind": "scheduled_match",
                        "amount": str(abs_amt),
                        "cash_txn_id": co.id,
                        "scheduled_id": s.id,
                        "scheduled_name": s.name,
                        "cash_account": accts[co.account_id].nickname,
                        "card_account": card_a.nickname if card_a else None,
                        "cash_date": co.txn_date.isoformat(),
                        "suggestion": (
                            f"Looks like payment for scheduled '{s.name}'. "
                            "Confirm transfer if the matching card credit posts."
                        ),
                        "action": "review",
                    }
                )
                break

    return {
        "as_of": as_of.isoformat(),
        "days": days,
        "count": len(candidates),
        "candidates": candidates,
        "principle": "Card payments should be transfers — not expense + income.",
    }


def apply_payment_as_transfer(
    session: Session,
    *,
    cash_txn_id: int,
    card_txn_id: int,
) -> dict[str, Any]:
    from financial_os.services.transfer_match import confirm_transfer_pair

    return confirm_transfer_pair(session, out_txn_id=cash_txn_id, in_txn_id=card_txn_id)
