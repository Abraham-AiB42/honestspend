"""Non-destructive statement-cycle defaults and import/Plaid learning.

Rules:
- If ``cycle_config_source == "user"``, never auto-change close/due/funding/policy.
- On new credit account: policy ``statement``, funding = profile's first checking,
  close/due from explicit overrides if present else 1/15.
- Optional: detect "Payment to {card}" history on cash to suggest funding.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, Transaction

DEFAULT_CLOSE_DAY = 1
DEFAULT_DUE_DAY = 15
DEFAULT_POLICY = "statement"
USER_SOURCE = "user"

_PAYMENT_TO = re.compile(
    r"\b(?:payment\s+to|pymt\s+to|pay\s+to|autopay)\b",
    re.I,
)


def _norm(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def is_user_cycle_locked(account: Account) -> bool:
    return (account.cycle_config_source or "").strip().lower() == USER_SOURCE


def default_funding_account_id(session: Session, profile_id: int | None) -> int | None:
    """Profile's first non-archived checking, else first cash-like (IFPP or savings/cash)."""
    if profile_id is None:
        return None
    pid = int(profile_id)
    base = session.query(Account).filter(
        Account.profile_id == pid,
        Account.archived_at.is_(None),
    )
    chk = (
        base.filter(Account.kind == "checking")
        .order_by(Account.id.asc())
        .first()
    )
    if chk:
        return int(chk.id)
    cash = (
        base.filter(
            (Account.is_cash_for_ifpp.is_(True))
            | (Account.kind.in_(("savings", "cash")))
        )
        .order_by(Account.id.asc())
        .first()
    )
    return int(cash.id) if cash else None


def suggest_funding_from_payment_history(
    session: Session,
    profile_id: int | None,
    card_name: str | None,
    *,
    lookback_days: int = 180,
    as_of: date | None = None,
) -> int | None:
    """Return cash account id that most often paid this card (Payment to {card})."""
    if profile_id is None or not card_name:
        return None
    card_key = _norm(card_name)
    if len(card_key) < 3:
        return None
    as_of = as_of or date.today()
    start = as_of - timedelta(days=lookback_days)
    cash_rows = (
        session.query(Account)
        .filter(
            Account.profile_id == int(profile_id),
            Account.archived_at.is_(None),
            Account.kind.in_(("checking", "savings", "cash")),
        )
        .all()
    )
    cash_ids = {int(a.id) for a in cash_rows}
    if not cash_ids:
        return None

    txns = (
        session.query(Transaction)
        .filter(
            Transaction.profile_id == int(profile_id),
            Transaction.account_id.in_(cash_ids),
            Transaction.txn_date >= start,
            Transaction.txn_date <= as_of,
            Transaction.amount < 0,
            Transaction.status != "void",
        )
        .all()
    )
    counts: dict[int, int] = defaultdict(int)
    for t in txns:
        payee = t.payee or ""
        pn = _norm(payee)
        if not pn:
            continue
        # Card name appears in payee, optionally with payment phrasing
        if card_key not in pn:
            # allow partial last token match (e.g. "capital one" vs "Payment to Capital One")
            tokens = card_key.split()
            if not tokens or not all(tok in pn for tok in tokens if len(tok) > 2):
                continue
        if _PAYMENT_TO.search(payee) or "card" in pn or card_key in pn:
            counts[int(t.account_id)] += 1

    if not counts:
        return None
    best = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))
    return best[0]


def apply_credit_cycle_defaults(
    session: Session,
    account: Account,
    *,
    source: str = "default",
    statement_close_day: int | None = None,
    payment_due_day: int | None = None,
    autopay_policy: str | None = None,
    payment_funding_account_id: int | None = None,
    suggest_funding: bool = True,
) -> dict[str, Any]:
    """Fill missing cycle fields on a credit account without overwriting user config.

    Only mutates when ``cycle_config_source != "user"``. Existing non-null
    close/due/funding/policy are left alone (fill-missing). Explicit kwargs are
    used when filling a blank field; otherwise defaults are 1 / 15 / statement /
    first checking (or payment-history suggestion).

    Returns a small result dict: ok, changed, reason?, fields set.
    """
    if account is None:
        return {"ok": False, "changed": False, "reason": "no_account"}
    if (account.kind or "").lower() != "credit":
        return {"ok": False, "changed": False, "reason": "not_credit"}

    if is_user_cycle_locked(account):
        return {
            "ok": True,
            "changed": False,
            "reason": "user_locked",
            "account_id": account.id,
        }

    filled: list[str] = []

    if account.statement_close_day is None:
        day = (
            int(statement_close_day)
            if statement_close_day is not None
            else DEFAULT_CLOSE_DAY
        )
        if day < 1:
            day = DEFAULT_CLOSE_DAY
        if day > 31:
            day = 31
        account.statement_close_day = day
        filled.append("statement_close_day")

    if account.payment_due_day is None:
        day = int(payment_due_day) if payment_due_day is not None else DEFAULT_DUE_DAY
        if day < 1:
            day = DEFAULT_DUE_DAY
        if day > 31:
            day = 31
        account.payment_due_day = day
        filled.append("payment_due_day")

    if not (account.autopay_policy or "").strip():
        pol = (autopay_policy or DEFAULT_POLICY).strip().lower() or DEFAULT_POLICY
        account.autopay_policy = pol
        filled.append("autopay_policy")

    if account.payment_funding_account_id is None:
        fid = payment_funding_account_id
        if fid is None and suggest_funding:
            fid = suggest_funding_from_payment_history(
                session, account.profile_id, account.nickname or account.institution
            )
        if fid is None:
            fid = default_funding_account_id(session, account.profile_id)
        if fid is not None:
            account.payment_funding_account_id = int(fid)
            filled.append("payment_funding_account_id")

    changed = bool(filled)
    src = (source or "default").strip().lower() or "default"
    if changed:
        account.cycle_config_source = src
        filled.append("cycle_config_source")
    elif not (account.cycle_config_source or "").strip():
        account.cycle_config_source = src
        filled.append("cycle_config_source")
        changed = True

    session.flush()
    return {
        "ok": True,
        "changed": changed,
        "account_id": account.id,
        "filled": filled,
        "cycle_config_source": account.cycle_config_source,
    }
