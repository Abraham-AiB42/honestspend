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

from financial_os.db import ScheduledItem, Transaction

ZERO = Decimal("0")
_NOISE = re.compile(r"[^a-z0-9\s]+")
_DIGITS = re.compile(r"\d+")
_MULTI_SPACE = re.compile(r"\s+")


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

    suggestions: list[dict[str, Any]] = []
    for key, txns in by_key.items():
        if len(txns) < min_occurrences:
            continue
        # Already scheduled?
        if key in known or any(k and (k in key or key in k) for k in known if len(k) >= 4):
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

        # Display name: most common original payee
        names: dict[str, int] = defaultdict(int)
        for t in txns:
            names[(t.payee or key).strip()] += 1
        display = max(names.items(), key=lambda x: x[1])[0]
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
                "reason": (
                    f"Seen {len(txns)}× in {lookback_days}d · "
                    f"~${avg.quantize(Decimal('0.01'))} · looks {cadence}"
                ),
                "action": "add_bill",
                "button_label": "Add as bill",
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
