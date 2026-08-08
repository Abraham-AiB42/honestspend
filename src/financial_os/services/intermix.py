"""Entity intermix tools: reimburse, owner draw/distribution, capital inject."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.orm import Session

from financial_os.db import Account, Category, Profile, Transaction

ZERO = Decimal("0")
IntermixKind = Literal["reimburse", "distribution", "capital_inject", "owner_draw"]


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _find_system_cat(session: Session, code: str) -> Category | None:
    return session.query(Category).filter(Category.code == code).first()


def _get_account(session: Session, account_id: int) -> Account:
    a = session.get(Account, account_id)
    if not a:
        raise ValueError(f"Account {account_id} not found")
    return a


def apply_intermix(
    session: Session,
    *,
    kind: IntermixKind,
    amount: Decimal,
    from_account_id: int,
    to_account_id: int,
    txn_date: date | None = None,
    memo: str | None = None,
) -> dict[str, Any]:
    """Create paired transfer transactions across accounts (possibly profiles).

    Conventions (amount always positive input):
      reimburse: business pays personal (business cash out, personal cash in)
      distribution / owner_draw: business → personal
      capital_inject: personal → business
    """
    amount = abs(_d(amount))
    if amount <= ZERO:
        raise ValueError("amount must be positive")
    txn_date = txn_date or date.today()
    src = _get_account(session, from_account_id)
    dst = _get_account(session, to_account_id)
    if src.id == dst.id:
        raise ValueError("from and to accounts must differ")

    transfer_cat = _find_system_cat(session, "SYS_TRANSFER")
    reimb_cat = _find_system_cat(session, "SYS_REIMBURSEMENT")
    dist_cat = _find_system_cat(session, "SYS_OWNER_DISTRIBUTION")
    contrib_cat = _find_system_cat(session, "SYS_OWNER_CONTRIBUTION")

    if kind == "reimburse":
        cat = reimb_cat or transfer_cat
        label = "Reimbursement"
    elif kind in ("distribution", "owner_draw"):
        cat = dist_cat or transfer_cat
        label = "Owner distribution" if kind == "distribution" else "Owner draw"
    elif kind == "capital_inject":
        cat = contrib_cat or transfer_cat
        label = "Capital inject"
    else:
        raise ValueError(f"Unknown intermix kind: {kind}")

    note = memo or f"{label}: {src.nickname} → {dst.nickname}"
    # Outflow from source
    t_out = Transaction(
        profile_id=src.profile_id,
        account_id=src.id,
        category_id=cat.id if cat else None,
        txn_date=txn_date,
        amount=-amount,
        payee=label,
        memo=note,
        status="cleared",
        is_transfer=True,
        external_id=None,
    )
    session.add(t_out)
    session.flush()

    t_in = Transaction(
        profile_id=dst.profile_id,
        account_id=dst.id,
        category_id=cat.id if cat else None,
        txn_date=txn_date,
        amount=amount,
        payee=label,
        memo=note,
        status="cleared",
        is_transfer=True,
        transfer_pair_id=t_out.id,
        external_id=None,
    )
    session.add(t_in)
    session.flush()
    t_out.transfer_pair_id = t_in.id

    # Balance updates (cash-like accounts)
    for acct, delta in ((src, -amount), (dst, amount)):
        if acct.kind == "credit":
            # paying a card from business would decrease card balance owed if dst is card...
            # For simplicity: cash accounts adjust; credit as dest increases available conceptually
            if delta < 0:
                acct.current_balance = _d(acct.current_balance) - delta  # paying card down
            else:
                acct.current_balance = _d(acct.current_balance) - delta  # charge? rare
            if acct.credit_limit is not None:
                acct.available_credit = _d(acct.credit_limit) - _d(acct.current_balance)
        else:
            acct.current_balance = _d(acct.current_balance) + delta

    session.flush()

    src_prof = session.get(Profile, src.profile_id)
    dst_prof = session.get(Profile, dst.profile_id)

    return {
        "ok": True,
        "kind": kind,
        "label": label,
        "amount": str(amount),
        "from": {
            "account_id": src.id,
            "account": src.nickname,
            "profile": src_prof.display_name if src_prof else src.profile_id,
        },
        "to": {
            "account_id": dst.id,
            "account": dst.nickname,
            "profile": dst_prof.display_name if dst_prof else dst.profile_id,
        },
        "out_txn_id": t_out.id,
        "in_txn_id": t_in.id,
        "note": note,
        "guidance": _guidance(kind),
    }


def _guidance(kind: str) -> str:
    return {
        "reimburse": "Business repaid personal for a business expense — not income, not an expense twice.",
        "distribution": "Owner distribution — equity/AAA, not a business expense on 1120-S.",
        "owner_draw": "Owner draw — treat carefully for S-corp; often better as W-2 or distribution.",
        "capital_inject": "Owner put personal cash into the business — capital contribution, not revenue.",
    }.get(kind, "")
