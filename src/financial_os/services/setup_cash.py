"""Setup wizard cash-account loop: create checking/savings/MM + bank guide hooks."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from financial_os.db import Account, Profile, Transaction
from financial_os.services.bank_guides import get_bank_guide, list_bank_guides

# UI type → Account.kind (+ default nickname stem)
CASH_TYPES: dict[str, dict[str, str]] = {
    "checking": {"kind": "checking", "label": "Checking", "stem": "Checking"},
    "savings": {"kind": "savings", "label": "Savings", "stem": "Savings"},
    "money_market": {"kind": "savings", "label": "Money market", "stem": "Money market"},
}


def _d(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _resolve_profile_id(session: Session, profile_id: int | None) -> int:
    if profile_id is not None:
        p = session.get(Profile, profile_id)
        if not p:
            raise ValueError("Profile not found")
        return int(profile_id)
    d = session.query(Profile).filter(Profile.is_default.is_(True)).first()
    if d:
        return int(d.id)
    p = session.query(Profile).filter(Profile.slug == "personal").first()
    if p:
        return int(p.id)
    raise ValueError("No profile — seed database first")


def list_cash_types() -> list[dict[str, str]]:
    return [
        {"id": k, "label": v["label"], "kind": v["kind"]}
        for k, v in CASH_TYPES.items()
    ]


def cash_accounts_status(
    session: Session,
    *,
    profile_id: int | None = None,
) -> dict[str, Any]:
    pid = _resolve_profile_id(session, profile_id)
    rows = (
        session.query(Account)
        .filter(
            Account.profile_id == pid,
            Account.archived_at.is_(None),
            Account.kind.in_(("checking", "savings", "cash")),
        )
        .order_by(Account.id)
        .all()
    )
    accounts = []
    for a in rows:
        txn_n = (
            session.query(func.count(Transaction.id))
            .filter(Transaction.account_id == a.id)
            .scalar()
            or 0
        )
        accounts.append(
            {
                "id": a.id,
                "nickname": a.nickname,
                "kind": a.kind,
                "institution": a.institution,
                "current_balance": str(_d(a.current_balance)),
                "is_cash_for_ifpp": bool(a.is_cash_for_ifpp),
                "transaction_count": int(txn_n),
                "imported": int(txn_n) > 0,
            }
        )
    need_import = [a for a in accounts if not a["imported"]]
    return {
        "profile_id": pid,
        "accounts": accounts,
        "count": len(accounts),
        "need_import": need_import,
        "all_imported": len(accounts) > 0 and len(need_import) == 0,
        "has_cash": len(accounts) > 0,
        "types": list_cash_types(),
        "bank_guides": list_bank_guides(),
        "import_hint": "Download ~90 days of transactions (CSV or OFX) for best bill detection.",
    }


def create_cash_account(
    session: Session,
    *,
    account_type: str,
    nickname: str | None = None,
    institution: str | None = None,
    bank_guide_id: str | None = None,
    current_balance: Decimal | float | str = 0,
    profile_id: int | None = None,
    make_primary: bool | None = None,
) -> dict[str, Any]:
    """Create one cash account for the CSV setup loop."""
    at = (account_type or "checking").strip().lower().replace(" ", "_")
    if at not in CASH_TYPES:
        raise ValueError("account_type must be checking, savings, or money_market")
    meta = CASH_TYPES[at]
    pid = _resolve_profile_id(session, profile_id)
    bal = _d(current_balance)

    # Default nickname
    name = (nickname or "").strip()
    if not name:
        existing = (
            session.query(Account)
            .filter(Account.profile_id == pid, Account.kind == meta["kind"])
            .count()
        )
        name = f"{meta['stem']} {existing + 1}" if existing else meta["stem"]

    inst = (institution or "").strip() or None
    if bank_guide_id and not inst:
        g = get_bank_guide(bank_guide_id)
        if g:
            inst = g.get("name")

    # First cash / first checking is IFPP primary unless make_primary False
    has_primary = (
        session.query(Account)
        .filter(
            Account.profile_id == pid,
            Account.is_cash_for_ifpp.is_(True),
            Account.archived_at.is_(None),
        )
        .count()
        > 0
    )
    if make_primary is None:
        primary = not has_primary and meta["kind"] == "checking"
        if not has_primary and meta["kind"] != "checking":
            # first cash of any kind if no checking yet
            primary = not has_primary
    else:
        primary = bool(make_primary)

    if primary and has_primary:
        # demote others
        for a in (
            session.query(Account)
            .filter(Account.profile_id == pid, Account.is_cash_for_ifpp.is_(True))
            .all()
        ):
            a.is_cash_for_ifpp = False

    row = Account(
        profile_id=pid,
        kind=meta["kind"],
        nickname=name,
        institution=inst,
        current_balance=bal,
        is_cash_for_ifpp=primary,
        include_in_net_worth=True,
    )
    session.add(row)
    session.flush()

    guide = get_bank_guide(bank_guide_id) if bank_guide_id else None
    if not guide and bank_guide_id:
        guide = get_bank_guide("generic")

    return {
        "ok": True,
        "account": {
            "id": row.id,
            "nickname": row.nickname,
            "kind": row.kind,
            "institution": row.institution,
            "current_balance": str(_d(row.current_balance)),
            "is_cash_for_ifpp": bool(row.is_cash_for_ifpp),
            "account_type": at,
            "bank_guide_id": bank_guide_id,
        },
        "guide": guide or get_bank_guide("generic"),
        "status": cash_accounts_status(session, profile_id=pid),
    }
