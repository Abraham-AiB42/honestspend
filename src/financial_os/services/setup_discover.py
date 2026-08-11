"""Skim cash outflows to propose cards, loans, bills, and recurring investments."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, Profile, ScheduledItem, Transaction
from financial_os.services.recurring_detect import detect_recurring, normalize_payee

ZERO = Decimal("0")

PAYMENT_OPTIONS = (
    "minimum",
    "fixed",
    "statement",
    "interest_saving",
)

# Card payment patterns (cash account paying a card)
_CARD_PAY = re.compile(
    r"\b("
    r"payment\s+to|pymt\s+to|pay\s+to|"
    r"credit\s*card\s*payment|cc\s*payment|card\s*payment|"
    r"amex|american\s*express|chase\s*card|citi\s*card|capital\s*one|"
    r"discover\s*card|apple\s*card|synchrony|barclays|"
    r"autopay.*card|cardmember"
    r")\b",
    re.I,
)

_LOAN_PAY = re.compile(
    r"\b("
    r"mortgage|home\s*loan|heloc|"
    r"auto\s*loan|car\s*payment|toyota\s*fin|honda\s*fin|ford\s*credit|"
    r"student\s*loan|navient|nelnet|great\s*lakes|aidvantage|"
    r"loan\s*payment|personal\s*loan|sofi\s*loan|lendingclub|"
    r"ally\s*auto|santander\s*consumer"
    r")\b",
    re.I,
)

_INVEST = re.compile(
    r"\b("
    r"fidelity|vanguard|schwab|robinhood|etrade|e\s*trade|"
    r"betterment|wealthfront|acorns|m1\s*finance|"
    r"ira\s*contribution|401k|brokerage|"
    r"recurring\s*invest|automatic\s*invest"
    r")\b",
    re.I,
)

_AUTOPAY_MAP = {
    "minimum": "min",
    "fixed": "min",  # fixed amount still uses min_payment field for amount
    "statement": "statement",
    "interest_saving": "promo_sink",
}


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _median(vals: list[Decimal]) -> Decimal:
    if not vals:
        return ZERO
    s = sorted(vals)
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _resolve_profile_id(session: Session, profile_id: int | None) -> int | None:
    if profile_id is not None:
        return int(profile_id)
    d = session.query(Profile).filter(Profile.is_default.is_(True)).first()
    if d:
        return int(d.id)
    p = session.query(Profile).filter(Profile.slug == "personal").first()
    return int(p.id) if p else None


def _cash_account_ids(session: Session, profile_id: int | None) -> set[int]:
    q = session.query(Account).filter(
        Account.archived_at.is_(None),
        Account.kind.in_(("checking", "savings", "cash")),
    )
    if profile_id is not None:
        q = q.filter(Account.profile_id == profile_id)
    return {int(a.id) for a in q.all()}


def _existing_credit_names(session: Session, profile_id: int | None) -> set[str]:
    q = session.query(Account).filter(
        Account.archived_at.is_(None),
        Account.kind.in_(("credit", "loan")),
    )
    if profile_id is not None:
        q = q.filter(Account.profile_id == profile_id)
    return {normalize_payee(a.nickname) for a in q.all()} | {
        normalize_payee(a.institution) for a in q.all() if a.institution
    }


def _classify(payee: str) -> str | None:
    """Return credit | loan | investment | None (generic bill)."""
    if _CARD_PAY.search(payee):
        return "credit"
    if _LOAN_PAY.search(payee):
        return "loan"
    if _INVEST.search(payee):
        return "investment"
    return None


def _display_name(norm: str, samples: list[str]) -> str:
    if samples:
        # Prefer longest original-ish sample cleaned
        best = max(samples, key=len)
        return best.strip()[:64] or norm.title()
    return norm.title()[:64]


def discover_liabilities(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
    lookback_days: int = 90,
) -> dict[str, Any]:
    """Propose cards/loans/bills/investments from cash outflows + recurring detect."""
    as_of = as_of or date.today()
    pid = _resolve_profile_id(session, profile_id)
    cash_ids = _cash_account_ids(session, pid)
    if not cash_ids:
        return {
            "proposals": [],
            "count": 0,
            "lookback_days": lookback_days,
            "message": "Add and import a cash account first.",
        }

    start = as_of - timedelta(days=lookback_days)
    q = session.query(Transaction).filter(
        Transaction.account_id.in_(cash_ids),
        Transaction.txn_date >= start,
        Transaction.txn_date <= as_of,
        Transaction.amount < 0,
        Transaction.is_transfer.is_(False),
        Transaction.status != "void",
    )
    if pid is not None:
        q = q.filter(Transaction.profile_id == pid)

    by_key: dict[str, list[Transaction]] = defaultdict(list)
    samples: dict[str, list[str]] = defaultdict(list)
    for t in q.all():
        key = normalize_payee(t.payee)
        if len(key) < 3:
            continue
        by_key[key].append(t)
        if t.payee:
            samples[key].append(t.payee)

    existing = _existing_credit_names(session, pid)
    proposals: list[dict[str, Any]] = []

    for key, txns in by_key.items():
        if key in existing:
            continue
        amounts = [abs(_d(t.amount)) for t in txns]
        dates = sorted({t.txn_date for t in txns})
        if not amounts:
            continue
        kind_hint = _classify(key)
        # Need 2+ hits for recurring, or 1 strong card/loan keyword hit
        if len(txns) < 2 and kind_hint is None:
            continue
        if len(txns) < 1:
            continue

        med = _median(amounts)
        avg = sum(amounts) / len(amounts)
        name = _display_name(key, samples.get(key, []))

        if kind_hint == "credit":
            prop_type = "credit"
            conf = min(0.95, 0.55 + 0.1 * len(txns))
            reason = f"Looks like a card payment ({len(txns)}× from cash, ~${med})"
            default_payment = "interest_saving"
        elif kind_hint == "loan":
            prop_type = "loan"
            conf = min(0.92, 0.5 + 0.1 * len(txns))
            reason = f"Looks like a loan payment ({len(txns)}×, ~${med})"
            default_payment = "fixed"
        elif kind_hint == "investment":
            prop_type = "recurring_investment"
            conf = min(0.9, 0.45 + 0.1 * len(txns))
            reason = f"Recurring investment / brokerage transfer ({len(txns)}×, ~${med})"
            default_payment = None
        else:
            # Generic recurring bill — only if 2+
            if len(txns) < 2:
                continue
            prop_type = "bill"
            conf = min(0.85, 0.4 + 0.08 * len(txns))
            reason = f"Recurring outflow ({len(txns)}×, ~${med})"
            default_payment = None

        # Cadence from gaps
        cadence = "monthly"
        if len(dates) >= 2:
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            avg_gap = sum(gaps) / len(gaps)
            if 5 <= avg_gap <= 9:
                cadence = "weekly"
            elif 12 <= avg_gap <= 17:
                cadence = "biweekly"

        next_due = dates[-1] + (
            timedelta(days=7)
            if cadence == "weekly"
            else timedelta(days=14)
            if cadence == "biweekly"
            else timedelta(days=30)
        )
        if next_due < as_of:
            next_due = as_of + timedelta(days=7)

        proposals.append(
            {
                "id": f"disc-{prop_type}-{key}"[:80],
                "type": prop_type,
                "name": name,
                "normalized_payee": key,
                "confidence": round(conf, 2),
                "occurrences": len(txns),
                "median_amount": str(med.quantize(Decimal("0.01"))),
                "avg_amount": str(avg.quantize(Decimal("0.01"))),
                "cadence": cadence,
                "suggested_next_date": next_due.isoformat(),
                "reason": reason,
                "default_payment_option": default_payment,
                "selected": conf >= 0.55,
            }
        )

    # Merge recurring_detect bills not already covered
    rec = detect_recurring(
        session,
        profile_id=pid,
        as_of=as_of,
        lookback_days=lookback_days,
        min_occurrences=2,
        limit=12,
    )
    seen = {p["normalized_payee"] for p in proposals}
    for s in rec.get("suggestions") or []:
        payee = normalize_payee(s.get("payee") or s.get("name"))
        if not payee or payee in seen or payee in existing:
            continue
        # Skip if already classified as card/loan above
        if _classify(payee):
            continue
        amt_abs = s.get("amount_abs") or s.get("typical_amount")
        if not amt_abs and s.get("amount"):
            # amount is negative expense string
            try:
                amt_abs = str(abs(Decimal(str(s["amount"]))))
            except Exception:
                amt_abs = "0"
        proposals.append(
            {
                "id": f"disc-bill-{payee}"[:80],
                "type": "bill",
                "name": (s.get("payee") or s.get("name") or payee)[:64],
                "normalized_payee": payee,
                "confidence": min(0.9, float(s.get("score") or 50) / 100.0 + 0.3),
                "occurrences": int(s.get("count") or s.get("occurrences") or 2),
                "median_amount": str(amt_abs or "0"),
                "avg_amount": str(amt_abs or "0"),
                "cadence": s.get("cadence") or "monthly",
                "suggested_next_date": s.get("suggested_next_date")
                or (as_of + timedelta(days=14)).isoformat(),
                "reason": s.get("reason") or "Detected recurring bill pattern",
                "default_payment_option": None,
                "selected": True,
            }
        )
        seen.add(payee)

    proposals.sort(key=lambda p: (-float(p["confidence"]), -int(p["occurrences"])))
    return {
        "as_of": as_of.isoformat(),
        "lookback_days": lookback_days,
        "profile_id": pid,
        "proposals": proposals[:25],
        "count": len(proposals[:25]),
        "payment_options": [
            {"id": "minimum", "label": "Minimum payment"},
            {"id": "fixed", "label": "Fixed payment"},
            {"id": "statement", "label": "Statement balance"},
            {"id": "interest_saving", "label": "Interest-saving (recommended)"},
        ],
        "message": (
            f"Found {len(proposals[:25])} possible card/loan/bill/investment pattern(s). "
            "Confirm what to create — nothing is added until you accept."
        ),
    }


def apply_discoveries(
    session: Session,
    *,
    accepted: list[dict[str, Any]],
    profile_id: int | None = None,
) -> dict[str, Any]:
    """Create accounts / scheduled items for accepted proposals.

    Each accepted item: {type, name, median_amount, cadence, suggested_next_date,
    payment_option?, payment_fixed_amount?, selected?}
    """
    pid = _resolve_profile_id(session, profile_id)
    if pid is None:
        raise ValueError("No profile")

    # Prefer primary cash for linking bills
    cash = (
        session.query(Account)
        .filter(
            Account.profile_id == pid,
            Account.is_cash_for_ifpp.is_(True),
            Account.archived_at.is_(None),
        )
        .first()
    )
    if not cash:
        cash = (
            session.query(Account)
            .filter(
                Account.profile_id == pid,
                Account.kind.in_(("checking", "savings", "cash")),
                Account.archived_at.is_(None),
            )
            .first()
        )

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in accepted:
        if item.get("selected") is False:
            continue
        ptype = (item.get("type") or "bill").lower()
        name = (item.get("name") or "Item").strip()[:64]
        amt = abs(_d(item.get("median_amount") or item.get("avg_amount") or 0))
        cadence = (item.get("cadence") or "monthly").lower()
        next_s = item.get("suggested_next_date")
        try:
            next_d = date.fromisoformat(str(next_s)[:10]) if next_s else date.today() + timedelta(days=14)
        except Exception:
            next_d = date.today() + timedelta(days=14)

        if ptype == "credit":
            # Idempotent: skip if credit with same normalized nickname exists
            existing_acct = (
                session.query(Account)
                .filter(
                    Account.profile_id == pid,
                    Account.kind == "credit",
                    Account.archived_at.is_(None),
                )
                .all()
            )
            if any(normalize_payee(a.nickname) == normalize_payee(name) for a in existing_acct):
                skipped.append({"type": "credit", "name": name, "reason": "already_exists"})
                continue
            opt = (item.get("payment_option") or item.get("default_payment_option") or "interest_saving")
            if opt not in PAYMENT_OPTIONS:
                opt = "interest_saving"
            # Only set fixed amount when user chose fixed; do NOT invent min from median payment
            fixed = _d(item.get("payment_fixed_amount")) if opt == "fixed" else None
            if opt == "fixed" and fixed <= 0 and amt > 0:
                fixed = amt
            bal = abs(_d(item.get("balance") or item.get("current_balance") or 0))
            limit = abs(_d(item.get("credit_limit") or 0)) or None
            row = Account(
                profile_id=pid,
                kind="credit",
                nickname=name,
                institution=name,
                current_balance=bal,
                credit_limit=limit,
                available_credit=(limit - bal) if limit is not None else None,
                is_cash_for_ifpp=False,
                payment_due_day=15,
                statement_close_day=1,
                payment_option=opt,
                payment_fixed_amount=fixed if opt == "fixed" else None,
                # min_payment only when fixed plan (known $) — never median historical payment
                min_payment=fixed if opt == "fixed" and fixed and fixed > 0 else None,
                autopay_policy=_AUTOPAY_MAP.get(opt),
            )
            session.add(row)
            session.flush()
            # Do NOT also schedule "{name} payment" — IFPP uses card balance/payoff path.
            # Double schedule would drain Safe to spend twice.
            created.append(
                {
                    "type": "credit",
                    "id": row.id,
                    "name": name,
                    "payment_option": opt,
                    "scheduled_payment": False,
                }
            )

        elif ptype == "loan":
            existing_loan = (
                session.query(Account)
                .filter(
                    Account.profile_id == pid,
                    Account.kind == "loan",
                    Account.archived_at.is_(None),
                )
                .all()
            )
            if any(normalize_payee(a.nickname) == normalize_payee(name) for a in existing_loan):
                skipped.append({"type": "loan", "name": name, "reason": "already_exists"})
                continue
            opt = (item.get("payment_option") or "fixed")
            if opt not in PAYMENT_OPTIONS:
                opt = "fixed"
            fixed = _d(item.get("payment_fixed_amount") or amt) if opt == "fixed" else None
            if opt == "fixed" and (fixed is None or fixed <= 0) and amt > 0:
                fixed = amt
            bal = abs(_d(item.get("balance") or item.get("current_balance") or 0))
            row = Account(
                profile_id=pid,
                kind="loan",
                nickname=name,
                institution=name,
                current_balance=bal,
                is_cash_for_ifpp=False,
                payment_option=opt,
                payment_fixed_amount=fixed if opt == "fixed" else None,
                min_payment=fixed if opt == "fixed" and fixed and fixed > 0 else None,
                autopay_policy=_AUTOPAY_MAP.get(opt, "min"),
            )
            session.add(row)
            session.flush()
            # Loan payments: schedule only if user provided a fixed amount (known obligation).
            # Avoid inventing bills from median without balance linkage that double-count IFPP.
            if opt == "fixed" and fixed and fixed > 0:
                pay_name = f"{name} payment"
                exist_bill = (
                    session.query(ScheduledItem)
                    .filter(
                        ScheduledItem.profile_id == pid,
                        ScheduledItem.active.is_(True),
                        ScheduledItem.name == pay_name,
                    )
                    .first()
                )
                if not exist_bill:
                    session.add(
                        ScheduledItem(
                            profile_id=pid,
                            account_id=cash.id if cash else None,
                            name=pay_name,
                            amount=-fixed,
                            next_date=next_d,
                            cadence=cadence
                            if cadence in ("weekly", "biweekly", "monthly", "yearly")
                            else "monthly",
                            certainty="fixed",
                            kind="expense",
                            active=True,
                            notes="Loan fixed payment from setup discover (linked liability account)",
                        )
                    )
            created.append({"type": "loan", "id": row.id, "name": name, "payment_option": opt})

        elif ptype == "recurring_investment":
            label = name if "invest" in name.lower() else f"{name} (Recurring Investments)"
            exist_bill = (
                session.query(ScheduledItem)
                .filter(
                    ScheduledItem.profile_id == pid,
                    ScheduledItem.active.is_(True),
                    ScheduledItem.name == label[:128],
                )
                .first()
            )
            if exist_bill:
                skipped.append({"type": "recurring_investment", "name": label, "reason": "already_exists"})
                continue
            bill = ScheduledItem(
                profile_id=pid,
                account_id=cash.id if cash else None,
                name=label[:128],
                amount=-amt if amt > 0 else Decimal("-0.01"),
                next_date=next_d,
                cadence=cadence if cadence in ("weekly", "biweekly", "monthly", "yearly") else "monthly",
                certainty="expected",
                kind="expense",
                active=True,
                notes="Recurring investment debit from setup discover",
            )
            session.add(bill)
            session.flush()
            created.append({"type": "recurring_investment", "id": bill.id, "name": label})

        else:
            # bill
            exist_bill = (
                session.query(ScheduledItem)
                .filter(
                    ScheduledItem.profile_id == pid,
                    ScheduledItem.active.is_(True),
                    ScheduledItem.name == name,
                )
                .first()
            )
            if exist_bill:
                skipped.append({"type": "bill", "name": name, "reason": "already_exists"})
                continue
            bill = ScheduledItem(
                profile_id=pid,
                account_id=cash.id if cash else None,
                name=name,
                amount=-amt if amt > 0 else Decimal("-0.01"),
                next_date=next_d,
                cadence=cadence if cadence in ("weekly", "biweekly", "monthly", "yearly") else "monthly",
                certainty="expected",
                kind="expense",
                active=True,
                notes="Bill from setup discover",
            )
            session.add(bill)
            session.flush()
            created.append({"type": "bill", "id": bill.id, "name": name})

    session.flush()
    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "count": len(created),
        "skipped_count": len(skipped),
        "message": (
            f"Created {len(created)} account(s)/bill(s)"
            + (f", skipped {len(skipped)} already present" if skipped else "")
            + "."
        ),
    }


def set_account_payment_option(
    session: Session,
    account_id: int,
    *,
    payment_option: str,
    payment_fixed_amount: Decimal | None = None,
) -> dict[str, Any]:
    opt = (payment_option or "").strip().lower()
    if opt not in PAYMENT_OPTIONS:
        raise ValueError("payment_option must be minimum, fixed, statement, or interest_saving")
    row = session.get(Account, account_id)
    if not row:
        raise ValueError("Account not found")
    if row.kind not in ("credit", "loan"):
        raise ValueError("payment_option only applies to credit or loan accounts")
    row.payment_option = opt
    row.autopay_policy = _AUTOPAY_MAP.get(opt)
    if opt == "fixed":
        row.payment_fixed_amount = _d(payment_fixed_amount) if payment_fixed_amount is not None else row.min_payment
        if row.payment_fixed_amount:
            row.min_payment = row.payment_fixed_amount
    session.flush()
    return {
        "id": row.id,
        "payment_option": row.payment_option,
        "payment_fixed_amount": str(row.payment_fixed_amount) if row.payment_fixed_amount is not None else None,
        "autopay_policy": row.autopay_policy,
    }
