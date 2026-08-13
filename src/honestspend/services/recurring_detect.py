"""Detect likely bills/subscriptions from transaction history (dream H1-A2).

Heuristic only — user confirms before creating scheduled items.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, ScheduledItem, Transaction

ZERO = Decimal("0")
_NOISE = re.compile(r"[^a-z0-9\s]+")
_DIGITS = re.compile(r"\d+")
_MULTI_SPACE = re.compile(r"\s+")
# Card/loan payment payees — never suggest as bills when a liability account exists
_LIABILITY_PAY = re.compile(
    r"\b("
    r"payment\s+to|pymt\s+to|pay\s+to|"
    r"credit\s*card\s*payment|cc\s*payment|card\s*payment|"
    r"amex|american\s*express|chase\s*card|citi\s*card|capital\s*one|"
    r"discover\s*card|apple\s*card|synchrony|barclays|"
    r"autopay.*card|cardmember|"
    r"mortgage|home\s*loan|heloc|auto\s*loan|car\s*payment|"
    r"student\s*loan|loan\s*payment|personal\s*loan"
    r")\b",
    re.I,
)


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def normalize_payee(payee: str | None) -> str:
    if not payee:
        return ""
    s = payee.lower().strip()
    s = _NOISE.sub(" ", s)
    s = _DIGITS.sub("", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    # Drop trailing store codes / short tails
    parts = [p for p in s.split() if len(p) > 1]
    return " ".join(parts[:5])


_CHECK_PAYEE = re.compile(
    r"(?:check|chk|cheque)\s*(?:#|no\.?|number)?\s*(\d+)\b",
    re.I,
)


def is_check_payee(payee: str | None) -> bool:
    return bool(_CHECK_PAYEE.search(payee or ""))


def categorize_payee_key(payee: str | None) -> str:
    """Group key for setup categorize. Check numbers stay distinct."""
    raw = (payee or "").strip()
    m = _CHECK_PAYEE.search(raw)
    if m:
        return f"check #{m.group(1)}"
    return normalize_payee(raw) or raw.lower()[:40]


def _guess_cadence(dates: list[date]) -> str | None:
    if len(dates) < 2:
        return None
    dates = sorted(dates)
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    if not gaps:
        return None
    avg = sum(gaps) / len(gaps)
    if 5 <= avg <= 9:
        return "weekly"
    if 12 <= avg <= 17:
        return "biweekly"
    if 25 <= avg <= 35:
        return "monthly"
    if 55 <= avg <= 70:
        return "monthly"  # bimonthly-ish → treat monthly suggestion carefully
    if 350 <= avg <= 380:
        return "yearly"
    # Allow near-monthly with variance
    if 20 <= avg <= 40:
        return "monthly"
    return None


def detect_recurring(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
    lookback_days: int = 180,
    min_occurrences: int = 2,
    limit: int = 8,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    start = as_of - timedelta(days=max(30, lookback_days))

    q = session.query(Transaction).filter(
        Transaction.txn_date >= start,
        Transaction.txn_date <= as_of,
        Transaction.amount < 0,
        Transaction.is_transfer.is_(False),
        Transaction.status != "void",
    )
    if profile_id is not None:
        q = q.filter(Transaction.profile_id == profile_id)

    by_key: dict[str, list[Transaction]] = defaultdict(list)
    for t in q.all():
        key = normalize_payee(t.payee)
        if len(key) < 3:
            continue
        by_key[key].append(t)

    # Existing scheduled names (normalize)
    sq = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if profile_id is not None:
        sq = sq.filter(ScheduledItem.profile_id == profile_id)
    known = {normalize_payee(s.name) for s in sq.all()}
    known |= {normalize_payee(s.name.split()[0]) for s in sq.all() if s.name}

    # Liability nicknames / institutions — skip "payment to Capital One" style bills
    lq = session.query(Account).filter(
        Account.archived_at.is_(None),
        Account.kind.in_(("credit", "loan")),
    )
    if profile_id is not None:
        lq = lq.filter(Account.profile_id == profile_id)
    liability_keys: set[str] = set()
    for a in lq.all():
        if a.nickname:
            liability_keys.add(normalize_payee(a.nickname))
        if a.institution:
            liability_keys.add(normalize_payee(a.institution))

    suggestions: list[dict[str, Any]] = []
    for key, txns in by_key.items():
        if len(txns) < min_occurrences:
            continue
        # Already scheduled?
        if key in known or any(k and (k in key or key in k) for k in known if len(k) >= 4):
            continue

        # Display name early for liability/payment filters
        names: dict[str, int] = defaultdict(int)
        for t in txns:
            names[(t.payee or key).strip()] += 1
        display = max(names.items(), key=lambda x: x[1])[0]

        # Skip card/loan payments (double-count with liability accounts / IFPP card path)
        if _LIABILITY_PAY.search(display) or _LIABILITY_PAY.search(key):
            continue
        if any(
            lk and len(lk) >= 3 and (lk in key or key in lk or lk in normalize_payee(display))
            for lk in liability_keys
        ):
            continue

        dates = sorted({t.txn_date for t in txns})
        cadence = _guess_cadence(dates)
        if cadence is None and len(dates) < 3:
            continue
        if cadence is None:
            cadence = "monthly"  # weak default only if 3+ hits

        amounts = [abs(_d(t.amount)) for t in txns]
        avg = sum(amounts) / len(amounts)
        # Prefer stable amounts (subscriptions)
        if avg <= 0:
            continue
        spread = max(amounts) - min(amounts)
        stability = float(1 - min(Decimal("1"), spread / avg)) if avg else 0.0
        last = max(dates)
        next_guess = last + (
            timedelta(days=30)
            if cadence == "monthly"
            else timedelta(days=7)
            if cadence == "weekly"
            else timedelta(days=14)
            if cadence == "biweekly"
            else timedelta(days=365)
        )

        score = len(txns) * 10 + stability * 20 + (15 if cadence == "monthly" else 5)

        # Majority account among hits — default schedule there (card-first users)
        acct_counts: dict[int, int] = defaultdict(int)
        for t in txns:
            if t.account_id is not None:
                acct_counts[int(t.account_id)] += 1
        suggested_account_id: int | None = None
        suggested_account_name: str | None = None
        suggested_account_kind: str | None = None
        pays_from = "cash"
        if acct_counts:
            suggested_account_id = max(acct_counts.items(), key=lambda x: x[1])[0]
            acct = session.get(Account, suggested_account_id)
            if acct:
                suggested_account_name = acct.nickname
                suggested_account_kind = (acct.kind or "other").lower()
                if suggested_account_kind == "credit":
                    pays_from = "card"
                elif suggested_account_kind == "loan":
                    pays_from = "loan"
                else:
                    pays_from = "cash"

        if pays_from == "card":
            reason = (
                f"Seen {len(txns)}× on card {suggested_account_name or ''} · "
                f"~${avg.quantize(Decimal('0.01'))} · {cadence}. "
                f"Default: schedule on this card (editable — won't double-hit Safe to spend)."
            )
            button = "Add on card"
        else:
            reason = (
                f"Seen {len(txns)}× in {lookback_days}d · "
                f"~${avg.quantize(Decimal('0.01'))} · looks {cadence}"
                + (
                    f" · default account {suggested_account_name}"
                    if suggested_account_name
                    else ""
                )
            )
            button = "Add as bill"

        suggestions.append(
            {
                "name": display[:128],
                "normalized": key,
                "amount": str((-avg).quantize(Decimal("0.01"))),  # expense negative
                "amount_abs": str(avg.quantize(Decimal("0.01"))),
                "cadence": cadence,
                "occurrences": len(txns),
                "last_seen": last.isoformat(),
                "suggested_next_date": next_guess.isoformat(),
                "stability_0_1": round(stability, 3),
                "score": round(score, 2),
                "suggested_account_id": suggested_account_id,
                "account_id": suggested_account_id,  # apply path default
                "suggested_account_name": suggested_account_name,
                "suggested_account_kind": suggested_account_kind,
                "pays_from": pays_from,
                "reason": reason.strip(),
                "action": "add_bill",
                "button_label": button,
            }
        )

    suggestions.sort(key=lambda x: (-x["score"], x["name"].lower()))
    top = suggestions[:limit]
    return {
        "as_of": as_of.isoformat(),
        "lookback_days": lookback_days,
        "count": len(top),
        "suggestions": top,
        "title": (
            f"{len(top)} possible bill{'s' if len(top) != 1 else ''} from history"
            if top
            else "No new recurring patterns"
        ),
        "reason": (
            "We spotted repeat charges that are not on your bill list yet. Add the real ones."
            if top
            else "Nothing new — or bills already scheduled."
        ),
        "needs_attention": len(top) > 0,
    }


def accept_recurring_suggestion(
    session: Session,
    *,
    name: str,
    amount: Decimal | str,
    cadence: str = "monthly",
    next_date: str | date | None = None,
    profile_id: int | None = None,
    account_id: int | None = None,
) -> dict[str, Any]:
    """Create a scheduled expense from a detected recurring pattern."""
    from honestspend.db import Account, Profile

    name = (name or "").strip()
    if not name:
        raise ValueError("name required")
    amt = -abs(_d(amount))
    if amt >= ZERO:
        raise ValueError("amount must be non-zero expense")
    cad = (cadence or "monthly").lower()
    if cad not in ("weekly", "biweekly", "semimonthly", "monthly", "yearly"):
        cad = "monthly"

    if profile_id is None:
        personal = session.query(Profile).filter(Profile.slug == "personal").first()
        if not personal:
            raise ValueError("No personal profile")
        profile_id = personal.id
    elif not session.get(Profile, profile_id):
        raise ValueError("Profile not found")

    notes_bits = ["Added from recurring detection"]
    if account_id is None:
        # Only when caller omits account — card-first flows pass suggested_account_id
        cash = (
            session.query(Account)
            .filter(
                Account.profile_id == profile_id,
                Account.archived_at.is_(None),
            )
            .filter(
                (Account.kind.in_(("checking", "cash", "savings")))
                | (Account.is_cash_for_ifpp.is_(True))
            )
            .order_by(Account.id)
            .first()
        )
        account_id = cash.id if cash else None
    else:
        acct = session.get(Account, account_id)
        if not acct or acct.profile_id != profile_id:
            raise ValueError("account_id must belong to profile")
        if (acct.kind or "").lower() == "credit":
            notes_bits.append("pays_from=card")

    if next_date is None:
        nxt = date.today() + timedelta(days=30 if cad == "monthly" else 7)
    elif isinstance(next_date, date):
        nxt = next_date
    else:
        nxt = date.fromisoformat(str(next_date)[:10])

    notes = " · ".join(notes_bits)

    # Idempotent by name + profile
    existing = (
        session.query(ScheduledItem)
        .filter(
            ScheduledItem.profile_id == profile_id,
            ScheduledItem.active.is_(True),
            ScheduledItem.name == name,
        )
        .first()
    )
    if existing:
        existing.amount = amt
        existing.cadence = cad
        existing.next_date = nxt
        existing.account_id = account_id or existing.account_id
        if notes and (
            not existing.notes or "recurring detection" in (existing.notes or "").lower()
        ):
            existing.notes = notes
        session.flush()
        return {
            "ok": True,
            "created": False,
            "updated": True,
            "scheduled_id": existing.id,
            "name": existing.name,
            "amount": str(existing.amount),
            "cadence": existing.cadence,
            "next_date": existing.next_date.isoformat(),
            "account_id": existing.account_id,
        }

    row = ScheduledItem(
        profile_id=profile_id,
        account_id=account_id,
        name=name[:128],
        amount=amt,
        next_date=nxt,
        cadence=cad,
        certainty="expected",
        kind="expense",
        notes=notes,
        active=True,
    )
    session.add(row)
    session.flush()
    return {
        "ok": True,
        "created": True,
        "updated": False,
        "scheduled_id": row.id,
        "name": row.name,
        "amount": str(row.amount),
        "cadence": row.cadence,
        "next_date": row.next_date.isoformat(),
        "account_id": row.account_id,
    }
