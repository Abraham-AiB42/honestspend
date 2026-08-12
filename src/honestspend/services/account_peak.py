"""Peak books balance for an account over a lookback window (and open cycle).

Walks non-void / non-pending ledger rows with the same sign rules as
`apply_amount_to_account` (cash: += amount; credit: -= amount).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Transaction

ZERO = Decimal("0")
TWOPLACES = Decimal("0.01")
_NON_BOOK_STATUSES = frozenset({"void", "pending"})


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return _d(v).quantize(TWOPLACES)


def _is_non_book(status: str | None) -> bool:
    return (status or "").lower() in _NON_BOOK_STATUSES


def _contribution(account_kind: str, amount: Decimal | Any) -> Decimal:
    """Balance delta for one txn under apply_amount_to_account rules."""
    amt = _d(amount)
    if account_kind != "credit":
        return amt
    return -amt


def peak_balance(
    session: Session,
    account_id: int,
    *,
    as_of: date | None = None,
    lookback_days: int = 90,
) -> dict:
    """Walk book-affecting txns; report current + peak in lookback / open cycle.

    Returns:
      current, peak_lookback, peak_lookback_date, peak_open_cycle
      (from last close to as_of if credit with close day), lookback_days
    """
    as_of = as_of or date.today()
    lookback_days = max(1, int(lookback_days))
    lookback_start = as_of - timedelta(days=lookback_days)

    account = session.get(Account, account_id)
    if account is None:
        raise ValueError(f"Account not found: {account_id}")

    kind = account.kind or ""
    open_start: date | None = None
    if kind == "credit" and account.statement_close_day:
        from honestspend.services.statement_cycle import previous_close_date

        close_day = int(account.statement_close_day)
        if close_day < 1:
            close_day = 1
        if close_day > 31:
            close_day = 31
        open_start = previous_close_date(as_of, close_day)

    rows = (
        session.query(Transaction)
        .filter(Transaction.account_id == account_id)
        .order_by(Transaction.txn_date.asc(), Transaction.id.asc())
        .all()
    )

    running = ZERO
    peak_lookback: Decimal | None = None
    peak_lookback_date: date | None = None
    peak_open_cycle: Decimal | None = None if open_start is None else None
    seeded_lookback = False
    seeded_open = False

    def _seed_lookback(bal: Decimal, when: date) -> None:
        nonlocal peak_lookback, peak_lookback_date, seeded_lookback
        peak_lookback = bal
        peak_lookback_date = when
        seeded_lookback = True

    def _seed_open(bal: Decimal) -> None:
        nonlocal peak_open_cycle, seeded_open
        peak_open_cycle = bal
        seeded_open = True

    def _maybe_peak_lookback(bal: Decimal, when: date) -> None:
        nonlocal peak_lookback, peak_lookback_date, seeded_lookback
        if peak_lookback is None or bal > peak_lookback:
            peak_lookback = bal
            peak_lookback_date = when
        seeded_lookback = True

    def _maybe_peak_open(bal: Decimal) -> None:
        nonlocal peak_open_cycle, seeded_open
        if peak_open_cycle is None or bal > peak_open_cycle:
            peak_open_cycle = bal
        seeded_open = True

    for row in rows:
        if _is_non_book(row.status):
            continue
        if row.txn_date > as_of:
            continue

        # Balance entering lookback / open-cycle windows counts as a peak candidate.
        if not seeded_lookback and row.txn_date >= lookback_start:
            _seed_lookback(running, lookback_start)
        if open_start is not None and not seeded_open and row.txn_date >= open_start:
            _seed_open(running)

        running = running + _contribution(kind, row.amount)

        if lookback_start <= row.txn_date <= as_of:
            _maybe_peak_lookback(running, row.txn_date)
        if open_start is not None and open_start <= row.txn_date <= as_of:
            _maybe_peak_open(running)

    # No in-window activity: balance was flat through the window → current is peak.
    if not seeded_lookback:
        peak_lookback = running
        peak_lookback_date = lookback_start if running != ZERO else None
    if open_start is not None and not seeded_open:
        peak_open_cycle = running

    return {
        "current": _q(running),
        "peak_lookback": _q(peak_lookback if peak_lookback is not None else ZERO),
        "peak_lookback_date": peak_lookback_date,
        "peak_open_cycle": (
            _q(peak_open_cycle) if peak_open_cycle is not None else None
        ),
        "lookback_days": lookback_days,
    }
