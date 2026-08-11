"""Expand recurring scheduled items into concrete occurrences for IFPP."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from financial_os.engine.ifpp import ScheduledView


def add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, monthrange(y, m)[1])
    return date(y, m, day)


def next_occurrence(d: date, cadence: str) -> date:
    c = (cadence or "monthly").lower()
    if c == "weekly":
        return d + timedelta(days=7)
    if c == "biweekly":
        return d + timedelta(days=14)
    if c == "semimonthly":
        # 1st <-> 15th style: jump ~15 days, clamp
        if d.day < 15:
            return d.replace(day=15)
        return add_months(d.replace(day=1), 1)
    if c == "yearly":
        return add_months(d, 12)
    # monthly default
    return add_months(d, 1)


def expand_scheduled(
    *,
    item_id: int,
    name: str,
    amount: Decimal,
    next_date: date,
    cadence: str,
    certainty: str,
    as_of: date,
    horizon_end: date,
    end_date: date | None = None,
    start_date: date | None = None,
    active: bool = True,
) -> list[ScheduledView]:
    """Generate occurrences in [as_of, horizon_end], respecting start/end_date and active.

    Occurrences strictly before start_date (when set) are skipped.
    Occurrences after end_date (when set) are excluded via hard_end.
    """
    if not active:
        return []

    hard_end = horizon_end
    if end_date is not None:
        hard_end = min(hard_end, end_date)

    # First date an occurrence may fire: max(as_of, start_date)
    effective_start = as_of
    if start_date is not None and start_date > effective_start:
        effective_start = start_date

    # Walk from next_date; roll forward to first occurrence >= effective_start
    cursor = next_date
    guard = 0
    while cursor < effective_start and guard < 500:
        cursor = next_occurrence(cursor, cadence)
        guard += 1
        if end_date and cursor > end_date:
            return []

    out: list[ScheduledView] = []
    guard = 0
    while cursor <= hard_end and guard < 500:
        if cursor >= effective_start and (start_date is None or cursor >= start_date):
            cert = certainty if certainty in ("fixed", "expected", "historical_avg") else "fixed"
            out.append(
                ScheduledView(
                    id=item_id,
                    name=name,
                    amount=amount,
                    on_date=cursor,
                    certainty=cert,  # type: ignore[arg-type]
                )
            )
        cursor = next_occurrence(cursor, cadence)
        if end_date and cursor > end_date:
            break
        guard += 1
    return out
