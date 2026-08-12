"""Cross-format duplicate detection for messy real-world imports.

People re-download overlapping date ranges, import both CSV and OFX, drop
statements plus full history, or click Import twice. We fingerprint every
row several ways and skip anything we already have — without punishing the
customer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

from sqlalchemy.orm import Session

from honestspend.db import Transaction

_PAYEE_NOISE = re.compile(
    r"\b("
    r"pos|purchase|debit|credit|ach|check|card|visa|mastercard|amex|"
    r"pending|posted|online|mobile|web|recurring|autopay|auto\s*pay|"
    r"payment\s*thank\s*you|thank\s*you"
    r")\b",
    re.I,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def quantize_amount(amount: Decimal | float | str | int) -> Decimal:
    try:
        d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except (InvalidOperation, ValueError):
        d = Decimal("0")
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_payee(payee: str | None) -> str:
    """Aggressive but stable payee key for duplicate matching."""
    s = (payee or "").lower().strip()
    s = _PAYEE_NOISE.sub(" ", s)
    s = _NON_ALNUM.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Drop trailing store numbers / city fragments often appended by banks
    parts = s.split()
    cleaned: list[str] = []
    for p in parts:
        if p.isdigit() and len(p) >= 3:
            continue
        cleaned.append(p)
    s = " ".join(cleaned)
    return s[:48]


def merchant_core(payee: str | None) -> str:
    """First solid token — bridges 'STARBUCKS #123 SEATTLE' vs 'POS STARBUCKS PURCHASE'."""
    np = normalize_payee(payee)
    words = [w for w in np.split() if len(w) >= 4]
    if not words:
        return np[:16]
    # Prefer longest early word (brand often first meaningful token)
    return max(words[:3], key=len)[:24]


def content_key(
    account_id: int,
    txn_date: date,
    amount: Decimal,
    payee: str | None,
) -> str:
    return f"c:{account_id}:{txn_date.isoformat()}:{quantize_amount(amount)}:{normalize_payee(payee)}"


def fingerprint_keys(
    *,
    account_id: int,
    txn_date: date,
    amount: Decimal,
    payee: str | None,
    fitid: str | None = None,
    bank_txn_id: str | None = None,
) -> set[str]:
    """Multiple keys so CSV/OFX/PDF of the same spend collide."""
    keys: set[str] = set()
    amt = quantize_amount(amount)
    d = txn_date.isoformat()
    np = normalize_payee(payee)
    core = merchant_core(payee)
    raw_payee = (payee or "").strip()

    # Content identity (cross-format) — primary
    keys.add(f"c:{account_id}:{d}:{amt}:{np}")
    keys.add(f"c:{account_id}:{d}:{amt}:{core}")
    # Also amount-absolute for rare sign flips between exports
    keys.add(f"cabs:{account_id}:{d}:{abs(amt)}:{np}")
    keys.add(f"cabs:{account_id}:{d}:{abs(amt)}:{core}")

    fit = (fitid or "").strip()
    if fit:
        keys.add(f"fit:{fit}")
        keys.add(f"fit:{account_id}:{fit}")
        keys.add(f"ofx:{account_id}:{fit}")

    bid = (bank_txn_id or "").strip()
    if bid:
        keys.add(f"bank:{bid}")
        keys.add(f"bank:{account_id}:{bid}")
        keys.add(f"csv:{account_id}:id:{bid}"[:200])

    # Legacy external_id shapes already in the DB
    if fit:
        keys.add(f"ofx:{account_id}:{fit}"[:200])
    keys.add(f"csv:{account_id}:{d}:{amt}:{raw_payee[:80]}"[:200])
    keys.add(f"ofx:{account_id}:{d}:{amt}:{raw_payee[:60]}"[:200])
    keys.add(f"pdf:{account_id}:{d}:{raw_payee[:48]}:{amt}"[:200])
    keys.add(f"xlsx:{d}:{amt}:{np}"[:200])

    return {k for k in keys if k}


@dataclass
class DedupeIndex:
    """In-memory + DB-backed index for one account during a batch import."""

    account_id: int
    keys: set[str] = field(default_factory=set)
    hits: int = 0
    checks: int = 0

    def is_duplicate(
        self,
        *,
        txn_date: date,
        amount: Decimal,
        payee: str | None,
        fitid: str | None = None,
        bank_txn_id: str | None = None,
    ) -> bool:
        self.checks += 1
        for k in fingerprint_keys(
            account_id=self.account_id,
            txn_date=txn_date,
            amount=amount,
            payee=payee,
            fitid=fitid,
            bank_txn_id=bank_txn_id,
        ):
            if k in self.keys:
                self.hits += 1
                return True
        # Near-date twin: same amount+payee within ±1 day (weekend/posting lag)
        amt = quantize_amount(amount)
        np = normalize_payee(payee)
        core = merchant_core(payee)
        for delta in (-1, 1):
            d2 = txn_date + timedelta(days=delta)
            for label in (np, core):
                if f"c:{self.account_id}:{d2.isoformat()}:{amt}:{label}" in self.keys:
                    self.hits += 1
                    return True
                if f"cabs:{self.account_id}:{d2.isoformat()}:{abs(amt)}:{label}" in self.keys:
                    self.hits += 1
                    return True
        return False

    def remember(
        self,
        *,
        txn_date: date,
        amount: Decimal,
        payee: str | None,
        fitid: str | None = None,
        bank_txn_id: str | None = None,
        external_id: str | None = None,
    ) -> None:
        self.keys |= fingerprint_keys(
            account_id=self.account_id,
            txn_date=txn_date,
            amount=amount,
            payee=payee,
            fitid=fitid,
            bank_txn_id=bank_txn_id,
        )
        if external_id:
            self.keys.add(external_id)

    def preferred_external_id(
        self,
        *,
        txn_date: date,
        amount: Decimal,
        payee: str | None,
        fitid: str | None = None,
        bank_txn_id: str | None = None,
        source: str = "imp",
    ) -> str:
        """Stable id stored on Transaction — prefer bank ids, else content key."""
        fit = (fitid or "").strip()
        if fit:
            return f"ofx:{self.account_id}:{fit}"[:200]
        bid = (bank_txn_id or "").strip()
        if bid:
            return f"csv:{self.account_id}:id:{bid}"[:200]
        return content_key(self.account_id, txn_date, amount, payee)[:200]


def load_dedupe_index(session: Session, account_id: int) -> DedupeIndex:
    """Load existing books fingerprints for an account (full history)."""
    idx = DedupeIndex(account_id=account_id)
    rows = (
        session.query(
            Transaction.external_id,
            Transaction.txn_date,
            Transaction.amount,
            Transaction.payee,
        )
        .filter(Transaction.account_id == account_id)
        .all()
    )
    for ext, d, amt, payee in rows:
        if ext:
            idx.keys.add(str(ext))
            # Extract FITID-like suffixes from known prefixes
            s = str(ext)
            if ":id:" in s:
                idx.keys.add("bank:" + s.split(":id:", 1)[-1])
            if s.startswith(f"ofx:{account_id}:") and s.count(":") >= 2:
                tail = s.split(":", 2)[-1]
                if tail and not re.match(r"^\d{4}-\d{2}-\d{2}", tail):
                    idx.keys.add(f"fit:{tail}")
                    idx.keys.add(f"fit:{account_id}:{tail}")
        if d is not None and amt is not None:
            try:
                da = d if isinstance(d, date) else d
                idx.remember(txn_date=da, amount=Decimal(str(amt)), payee=payee)
            except Exception:
                pass
    return idx


def load_dedupe_indexes(
    session: Session, account_ids: Iterable[int]
) -> dict[int, DedupeIndex]:
    return {int(aid): load_dedupe_index(session, int(aid)) for aid in account_ids}


def human_dup_summary(*, created: int, skipped: int, files: int = 1) -> str:
    """Plain language for average customers."""
    if skipped <= 0:
        if created <= 0:
            return "No new transactions found (everything may already be in your books)."
        return f"Added {created} new transaction{'s' if created != 1 else ''}."
    if created <= 0:
        return (
            f"Skipped {skipped} duplicate{'s' if skipped != 1 else ''} — "
            "those were already in your books (overlapping downloads are normal)."
        )
    return (
        f"Added {created} new · skipped {skipped} duplicate"
        f"{'s' if skipped != 1 else ''} from overlapping files. "
        "Your books stay clean even if you re-download the same months."
    )
