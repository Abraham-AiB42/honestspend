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
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Transaction

DEFAULT_CLOSE_DAY = 1
DEFAULT_DUE_DAY = 15
DEFAULT_POLICY = "statement"
USER_SOURCE = "user"

# Payment/transfer-like payee phrasing (not mere card-name presence).
_PAYMENT_TO = re.compile(
    r"\b(?:"
    r"payment\s+to|pymt\s+to|pay\s+to|autopay|"
    r"automatic\s+payment|auto\s+pay(?:ment)?|"
    r"web\s+payment|online\s+payment|"
    r"payment|pymt|"
    r"transfer\s+to|xfer\s+to|ach\s+(?:pmt|payment|pymt)|online\s+pmt|"
    r"thank\s+you|"
    r"direct\s*pay|"
    r"full\s+balance"
    r")\b",
    re.I,
)

_STATEMENT_AUTOPAY = re.compile(
    r"direct\s*pay(?:ment)?(?:\s+full\s+balance)?|"
    r"full\s+balance|"
    r"pay\s+in\s+full",
    re.I,
)

_BRAND_TOKENS: dict[str, tuple[str, ...]] = {
    "amex": ("american express", "amex"),
    "chase": ("chase", "jpmorgan", "jp morgan", "ink"),
    "discover": ("discover",),
    "capital one": ("capital one",),
    "citi": ("citi", "citibank"),
    "wells": ("wells fargo",),
    "apple": ("apple card",),
    "bankcard": ("bankcard", "first bankcard", "1st bankcard"),
}


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


def _last4_from_account(account: Account | None) -> str | None:
    if account is None:
        return None
    blob = " ".join(
        str(x or "")
        for x in (account.nickname, account.institution, account.external_id)
    )
    m = re.search(r"(?:…|\.{2,}|ending\s*(?:in)?\s*)(\d{4})\b", blob, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4})\s*$", blob.replace(" ", ""))
    return m.group(1) if m else None


def _brand_needles(card_name: str | None, institution: str | None) -> list[str]:
    needles: list[str] = []
    blob = _norm(f"{card_name or ''} {institution or ''}")
    for _key, toks in _BRAND_TOKENS.items():
        if any(t in blob for t in toks):
            needles.extend(toks)
    for part in (card_name, institution):
        n = _norm(part)
        if len(n) >= 4:
            needles.append(n)
    # unique, longer first
    seen: set[str] = set()
    out: list[str] = []
    for n in sorted(needles, key=len, reverse=True):
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _payee_hits_card(payee: str, *, card_key: str, last4: str | None, brands: list[str]) -> bool:
    pn = _norm(payee)
    if not pn:
        return False
    if last4 and last4 in (payee or ""):
        return True
    if card_key and len(card_key) >= 3 and card_key in pn:
        return True
    tokens = [t for t in card_key.split() if len(t) > 2]
    if tokens and all(tok in pn for tok in tokens):
        return True
    return any(b in pn for b in brands if len(b) >= 4)


def suggest_funding_from_payment_history(
    session: Session,
    profile_id: int | None,
    card_name: str | None,
    *,
    lookback_days: int = 180,
    as_of: date | None = None,
    last4: str | None = None,
    institution: str | None = None,
    card_account_id: int | None = None,
) -> int | None:
    """Return cash account id that most often paid this card.

    Uses payee text (Payment to {card} / last-4 / brand) and same-amount
    cash-out ↔ card-in pairs. Does not invent a default checking account.
    """
    if profile_id is None:
        return None
    card = session.get(Account, card_account_id) if card_account_id else None
    if card is not None:
        card_name = card_name or card.nickname
        institution = institution or card.institution
        last4 = last4 or _last4_from_account(card)
    card_key = _norm(card_name)
    brands = _brand_needles(card_name, institution)
    if len(card_key) < 3 and not last4 and not brands:
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

    scores: dict[int, int] = defaultdict(int)
    cash_txns = (
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
    for t in cash_txns:
        payee = t.payee or ""
        if not _PAYMENT_TO.search(payee):
            continue
        if not _payee_hits_card(payee, card_key=card_key, last4=last4, brands=brands):
            continue
        scores[int(t.account_id)] += 2

    if card is not None:
        card_pays = (
            session.query(Transaction)
            .filter(
                Transaction.account_id == int(card.id),
                Transaction.txn_date >= start,
                Transaction.txn_date <= as_of,
                Transaction.amount > 0,
                Transaction.status != "void",
            )
            .all()
        )
        for cp in card_pays:
            pay = cp.payee or ""
            if (
                not _PAYMENT_TO.search(pay)
                and "thank" not in pay.lower()
                and not _STATEMENT_AUTOPAY.search(pay)
            ):
                continue
            abs_amt = abs(cp.amount)
            for t in cash_txns:
                if abs(abs(t.amount) - abs_amt) > Decimal("0.01"):
                    continue
                if abs((t.txn_date - cp.txn_date).days) > 7:
                    continue
                scores[int(t.account_id)] += 3

    if not scores:
        return None
    best = max(scores.items(), key=lambda kv: (kv[1], -kv[0]))
    if best[1] < 2:
        return None
    return best[0]


def learn_card_funding_after_import(session: Session) -> dict[str, Any]:
    """Fill blank pay-from cash on cards from imported payment history."""
    filled: list[dict[str, Any]] = []
    cards = (
        session.query(Account)
        .filter(Account.kind == "credit", Account.archived_at.is_(None))
        .order_by(Account.id.asc())
        .all()
    )
    for card in cards:
        if is_user_cycle_locked(card):
            continue
        if card.payment_funding_account_id is not None:
            continue
        fid = suggest_funding_from_payment_history(
            session,
            card.profile_id,
            card.nickname or card.institution,
            last4=_last4_from_account(card),
            institution=card.institution,
            card_account_id=int(card.id),
        )
        if fid is None:
            continue
        card.payment_funding_account_id = int(fid)
        if not (card.cycle_config_source or "").strip():
            card.cycle_config_source = "import"
        filled.append({"account_id": int(card.id), "funding_account_id": int(fid)})
    if filled:
        session.flush()
    policy = learn_statement_autopay_from_payees(session)
    return {
        "ok": True,
        "filled": len(filled),
        "cards": filled,
        "statement_autopay": policy.get("cards") or [],
    }


def learn_statement_autopay_from_payees(session: Session) -> dict[str, Any]:
    """DIRECTPAY FULL BALANCE (and kin) on a card → pay statement in full."""
    updated: list[dict[str, Any]] = []
    cards = (
        session.query(Account)
        .filter(Account.kind == "credit", Account.archived_at.is_(None))
        .order_by(Account.id.asc())
        .all()
    )
    for card in cards:
        if is_user_cycle_locked(card):
            continue
        pol = (card.autopay_policy or "").strip().lower()
        if pol in ("statement", "promo_sink", "books", "fixed"):
            continue
        n = 0
        for t in (
            session.query(Transaction)
            .filter(
                Transaction.account_id == int(card.id),
                Transaction.status != "void",
            )
            .order_by(Transaction.id.desc())
            .limit(80)
            .all()
        ):
            if _STATEMENT_AUTOPAY.search(t.payee or ""):
                n += 1
        if n < 2:
            continue
        card.autopay_policy = "statement"
        if not (card.cycle_config_source or "").strip():
            card.cycle_config_source = "import"
        updated.append({"account_id": int(card.id), "hits": n})
    if updated:
        session.flush()
    return {"ok": True, "updated": len(updated), "cards": updated}


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
    invent_calendar: bool = True,
) -> dict[str, Any]:
    """Fill missing cycle fields on a credit account without overwriting user config.

    Only mutates when ``cycle_config_source != "user"``. Existing non-null
    close/due/funding/policy are left alone (fill-missing). Explicit kwargs are
    used when filling a blank field. When ``invent_calendar`` is True, blanks
    become 1 / 15 / first checking. When False (Plaid/CSV), leave due/funding
    empty unless the caller passed them — Simple Home confirms.

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

    if account.statement_close_day is None and (
        statement_close_day is not None or invent_calendar
    ):
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

    if account.payment_due_day is None and (
        payment_due_day is not None or invent_calendar
    ):
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

    if account.payment_funding_account_id is None and (
        payment_funding_account_id is not None or invent_calendar
    ):
        fid = payment_funding_account_id
        if fid is None and suggest_funding:
            fid = suggest_funding_from_payment_history(
                session,
                account.profile_id,
                account.nickname or account.institution,
                last4=_last4_from_account(account),
                institution=account.institution,
                card_account_id=int(account.id) if account.id else None,
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
