"""Detect cash→card payments that match scheduled bills or card balances."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Category, ScheduledItem, Transaction

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


_PAY_HINT = (
    "payment",
    "thank you",
    "autopay",
    "auto pay",
    "directpay",
    "direct pay",
    "full balance",
    "transfer to",
    "transfer from",
    "from share",
    "to share",
    "deposit transfer",
    "withdrawal transfer",
    "balance transfer",
)
_SHARE_HINT = (
    "from share",
    "to share",
    "deposit transfer",
    "withdrawal transfer",
)
_ISSUER_GROUPS: dict[str, tuple[str, ...]] = {
    "amex": ("american express", "amex", "blue business", "hilton honors", "hilton"),
    "chase": ("chase", "jpmorgan", "jp morgan", "ink", "prime visa"),
    "discover": ("discover",),
    "capital one": ("capital one",),
    "wells": ("wells fargo", "wells"),
    "home depot": ("home depot",),
    "hyundai": ("hyundai", "palisade"),
    "rocket": ("rocket",),
    "target": ("target",),
    "scheels": ("scheels", "first bankcard", "fnbo"),
    "apple": ("apple card", "goldman"),
}
CASH_KINDS = {"checking", "savings", "cash"}
DEST_KINDS = {"credit", "loan"}


def _is_synthetic_account(acct: Account) -> bool:
    n = (acct.nickname or "").strip().lower()
    return n.startswith("payment to") or n.startswith("tax payment to")


def payment_dest_hint(payee: str) -> str | None:
    m = re.search(r"(?:tax\s+)?payment to\s+(.+)$", payee or "", re.I)
    return m.group(1).strip() if m else None


def _looks_like_payment(payee: str) -> bool:
    p = (payee or "").lower()
    return any(h in p for h in _PAY_HINT)


def _payee_penalty(payee: str) -> int:
    p = (payee or "").lower()
    if any(x in p for x in ("returned", "reversal", "return us", "rev -", "nsf")):
        return -25
    return 0


def _is_share_move(payee: str) -> bool:
    p = (payee or "").lower()
    return any(h in p for h in _SHARE_HINT)


def _issuer_group(text: str) -> str | None:
    blob = (text or "").lower()
    for key, aliases in _ISSUER_GROUPS.items():
        if any(a in blob for a in aliases):
            return key
    return None


def _acct_blob(acct: Account) -> str:
    return f"{acct.nickname or ''} {acct.institution or ''}".lower()


def _acct_quality(acct: Account) -> int:
    if _is_synthetic_account(acct):
        return -100
    nick = acct.nickname or ""
    if "…" in nick or "..." in nick or "·" in nick:
        return 0
    return 4


def _payee_names_account(payee: str, acct: Account) -> int:
    if _is_synthetic_account(acct):
        return 0
    hint = (payee or "").lower()
    dest = payment_dest_hint(payee)
    if dest:
        hint = dest.lower()
    if not hint:
        return 0
    nick = (acct.nickname or "").lower()
    inst = (acct.institution or "").lower()
    score = 0
    for token in (nick, inst):
        t = re.sub(r"[·.…*x]+", " ", token).strip()
        t = re.sub(r"\s+\d{3,4}$", "", t).strip()
        if len(t) >= 5 and t in hint:
            score = max(score, min(len(t), 18))
        elif len(t) >= 5 and hint in t:
            score = max(score, min(len(hint), 18))
    blob = _acct_blob(acct)
    group = _issuer_group(hint)
    if group and any(a in blob for a in _ISSUER_GROUPS[group]):
        score = max(score, 16)
    return score


def _share_num(text: str) -> str | None:
    m = re.search(r"share\s*0*(\d{1,4})\b", text or "", re.I)
    if not m:
        return None
    return m.group(1).lstrip("0") or "0"


def _acct_tail_digits(acct: Account) -> str:
    nick = (acct.nickname or "").replace("…", "").replace(".", "")
    m = re.search(r"(\d{3,4})\s*$", nick)
    if not m:
        return ""
    return m.group(1).lstrip("0") or "0"


def infer_dest_account_name(
    session: Session,
    payee: str,
    *,
    exclude_ids: set[int] | None = None,
    from_account_id: int | None = None,
) -> str | None:
    """Best real books account named by a 'Payment to …' / share payee."""
    exclude_ids = exclude_ids or set()
    dest = payment_dest_hint(payee)
    hint = (dest or payee or "").strip()
    if not hint:
        return None
    hint_l = hint.lower()
    loanish = any(
        k in hint_l
        for k in ("loan", "hyundai", "palisade", "mortgage", "rocket", "education", "student")
    )
    share_n = _share_num(payee)
    affinity: dict[int, int] = {}
    dest_l = (dest or "").lower()
    if dest_l:
        q = session.query(Transaction).filter(
            Transaction.transfer_pair_id.is_not(None),
            Transaction.amount < ZERO,
        )
        if from_account_id is not None:
            q = q.filter(Transaction.account_id == from_account_id)
        for t in q.limit(300).all():
            ph = payment_dest_hint(t.payee or "")
            if not ph or ph.lower() != dest_l:
                continue
            pair = session.get(Transaction, t.transfer_pair_id)
            if pair and pair.account_id:
                affinity[pair.account_id] = affinity.get(pair.account_id, 0) + 4
    scored: list[tuple[int, str]] = []
    for a in session.query(Account).filter(Account.archived_at.is_(None)).all():
        if a.id in exclude_ids or _is_synthetic_account(a):
            continue
        score = _payee_names_account(payee, a)
        if share_n:
            tail = _acct_tail_digits(a)
            if tail and tail == share_n and a.kind in CASH_KINDS:
                score = max(score, 24)
        elif _is_share_move(payee) and a.kind in CASH_KINDS:
            score = max(score, 4)
        if a.kind == "loan" and not loanish:
            score -= 12
        score += affinity.get(a.id, 0)
        if score > 0:
            scored.append((score + _acct_quality(a), a.nickname))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] >= 8:
        return scored[0][1]
    if dest:
        return dest[:60]
    return None


def _pair_score(
    o: Transaction,
    i: Transaction,
    ao: Account,
    ai: Account,
    *,
    days: int,
) -> int:
    if _is_synthetic_account(ai) or _is_synthetic_account(ao):
        return 0
    out_pay = o.payee or ""
    in_pay = i.payee or ""
    share_out = _is_share_move(out_pay)
    share_in = _is_share_move(in_pay)
    if share_out or share_in:
        if not (share_out and share_in):
            return 0
        if ao.kind not in CASH_KINDS or ai.kind not in CASH_KINDS:
            return 0
        out_n = _share_num(out_pay)
        in_n = _share_num(in_pay)
        ai_tail = _acct_tail_digits(ai)
        ao_tail = _acct_tail_digits(ao)
        if out_n and ai_tail and out_n != ai_tail:
            return 0
        if in_n and ao_tail and in_n != ao_tail:
            return 0
        gap = abs((o.txn_date - i.txn_date).days)
        score = 20 + max(0, 6 - gap) + _acct_quality(ai)
        if out_n and ai_tail and out_n == ai_tail:
            score += 8
        if in_n and ao_tail and in_n == ao_tail:
            score += 8
        return score

    dest_hint = payment_dest_hint(out_pay) or payment_dest_hint(in_pay)
    name = _payee_names_account(out_pay, ai) + _payee_names_account(in_pay, ao)
    if dest_hint and name < 8:
        return 0
    both_pay = _looks_like_payment(out_pay) and _looks_like_payment(in_pay)
    one_pay = _looks_like_payment(out_pay) or _looks_like_payment(in_pay)
    if dest_hint is None and name < 8:
        # Amount + "payment" on one side is not enough (checks, random charges).
        if not both_pay:
            return 0
        credit_to_credit = ao.kind in DEST_KINDS and ai.kind in DEST_KINDS
        cash_to_dest = (ao.kind in CASH_KINDS and ai.kind in DEST_KINDS) or (
            ao.kind in DEST_KINDS and ai.kind in CASH_KINDS
        )
        if not (credit_to_credit or cash_to_dest):
            return 0

    score = name
    if both_pay:
        score += 10
    elif one_pay:
        score += 4
    if ao.kind in CASH_KINDS and ai.kind in DEST_KINDS:
        score += 8
    elif ao.kind in DEST_KINDS and ai.kind in CASH_KINDS:
        score += 8
    elif ao.kind in DEST_KINDS and ai.kind in DEST_KINDS and both_pay:
        score += 8
    elif ao.kind in CASH_KINDS and ai.kind in CASH_KINDS and both_pay:
        score += 6
    gap = abs((o.txn_date - i.txn_date).days)
    score += max(0, 4 - gap)
    score += _acct_quality(ai)
    if dest_hint and name >= 8:
        score += 4
    score += _payee_penalty(out_pay) + _payee_penalty(in_pay)
    if days and gap > days:
        return 0
    return score


def _mark_unpaired_account_moves(
    session: Session,
    rows: list[Transaction],
    accts: dict[int, Account],
    *,
    used: set[int],
    limit: int,
) -> list[dict[str, Any]]:
    transfer_cat = session.query(Category).filter(Category.code == "SYS_TRANSFER").first()
    cc_cat = session.query(Category).filter(Category.code == "SYS_CC_PAYMENT").first()
    marked: list[dict[str, Any]] = []
    for t in rows:
        if t.id in used or t.transfer_pair_id is not None:
            continue
        if "[skip-auto-link]" in (t.memo or ""):
            continue
        if len(marked) >= limit:
            break
        acct = accts.get(t.account_id)
        if not acct:
            continue
        payee = t.payee or ""
        dest_name = None
        cat = transfer_cat
        if _is_share_move(payee) and acct.kind in CASH_KINDS:
            dest_name = infer_dest_account_name(session, payee, exclude_ids={acct.id})
        elif payment_dest_hint(payee):
            dest_name = infer_dest_account_name(session, payee, exclude_ids={acct.id})
            if not dest_name or dest_name.lower() == payment_dest_hint(payee).lower():
                # Only mark when the dest is a real books account, not a vendor.
                real = False
                for a in accts.values():
                    if a.id == acct.id or _is_synthetic_account(a):
                        continue
                    if _payee_names_account(payee, a) >= 8:
                        dest_name = a.nickname
                        real = True
                        if a.kind in DEST_KINDS:
                            cat = cc_cat or transfer_cat
                        break
                if not real:
                    continue
            else:
                cat = cc_cat or transfer_cat
        else:
            continue
        t.is_transfer = True
        if t.category_id is None and cat is not None:
            t.category_id = cat.id
        used.add(t.id)
        marked.append(
            {
                "from_account": acct.nickname,
                "to_account": dest_name,
                "amount": str(abs(_d(t.amount))),
                "out_txn_id": t.id if _d(t.amount) < ZERO else None,
                "in_txn_id": t.id if _d(t.amount) > ZERO else None,
                "out_date": t.txn_date.isoformat(),
                "in_date": t.txn_date.isoformat(),
                "out_payee": payee,
                "in_payee": "",
                "unpaired": True,
            }
        )
    return marked


def auto_link_account_payments(
    session: Session,
    *,
    days: int = 14,
    as_of: date | None = None,
    limit: int = 250,
) -> dict[str, Any]:
    """Pair cash/share outflows with the matching inflow on another books account."""
    from honestspend.services.transfer_match import confirm_transfer_pair

    as_of = as_of or date.today()
    start = as_of - timedelta(days=800)
    accts = {
        a.id: a
        for a in session.query(Account).filter(Account.archived_at.is_(None)).all()
    }
    rows = (
        session.query(Transaction)
        .filter(
            Transaction.txn_date >= start,
            Transaction.txn_date <= as_of + timedelta(days=30),
            Transaction.transfer_pair_id.is_(None),
            Transaction.status != "void",
        )
        .order_by(Transaction.txn_date.desc())
        .limit(4000)
        .all()
    )
    by_amt: dict[str, list[Transaction]] = {}
    for t in rows:
        key = str(abs(_d(t.amount)).quantize(Decimal("0.01")))
        by_amt.setdefault(key, []).append(t)

    linked = 0
    pairs: list[dict[str, Any]] = []
    used: set[int] = set()
    for group in by_amt.values():
        outs = [t for t in group if _d(t.amount) < ZERO and t.id not in used]
        ins = [t for t in group if _d(t.amount) > ZERO and t.id not in used]
        for o in outs:
            if o.id in used:
                continue
            best: tuple[int, Transaction] | None = None
            for i in ins:
                if i.id in used or i.account_id == o.account_id:
                    continue
                if "[skip-auto-link]" in (o.memo or "") or "[skip-auto-link]" in (i.memo or ""):
                    continue
                if abs((o.txn_date - i.txn_date).days) > days:
                    continue
                ao = accts.get(o.account_id)
                ai = accts.get(i.account_id)
                if not ao or not ai:
                    continue
                score = _pair_score(o, i, ao, ai, days=days)
                if score < 16:
                    continue
                if best is None or score > best[0]:
                    best = (score, i)
            if best is None:
                continue
            inn = best[1]
            try:
                confirm_transfer_pair(session, out_txn_id=o.id, in_txn_id=inn.id)
            except ValueError:
                continue
            used.add(o.id)
            used.add(inn.id)
            linked += 1
            ao = accts.get(o.account_id)
            ai = accts.get(inn.account_id)
            pairs.append(
                {
                    "from_account": ao.nickname if ao else str(o.account_id),
                    "to_account": ai.nickname if ai else str(inn.account_id),
                    "amount": str(abs(_d(o.amount))),
                    "out_txn_id": o.id,
                    "in_txn_id": inn.id,
                    "out_date": o.txn_date.isoformat(),
                    "in_date": inn.txn_date.isoformat(),
                    "out_payee": o.payee or "",
                    "in_payee": inn.payee or "",
                }
            )
            if linked >= limit:
                session.flush()
                return {"linked": linked, "pairs": pairs, "marked_unpaired": 0}
    unpaired = _mark_unpaired_account_moves(
        session, rows, accts, used=used, limit=max(0, limit - linked)
    )
    session.flush()
    return {
        "linked": linked,
        "pairs": pairs + unpaired,
        "marked_unpaired": len(unpaired),
    }


def apply_payment_as_transfer(
    session: Session,
    *,
    cash_txn_id: int,
    card_txn_id: int,
) -> dict[str, Any]:
    from honestspend.services.transfer_match import confirm_transfer_pair

    return confirm_transfer_pair(session, out_txn_id=cash_txn_id, in_txn_id=card_txn_id)
