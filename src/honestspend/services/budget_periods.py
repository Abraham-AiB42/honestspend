"""Period windows for daily / weekly / monthly budgets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


# Python date.weekday(): Mon=0 … Sun=6
DEFAULT_WORKDAYS = 0b0011111  # Mon–Fri = bits 0..4 = 31
DEFAULT_WEEK_START = 0  # Monday


@dataclass(frozen=True)
class PeriodWindow:
    period: str
    start: date
    end: date  # inclusive
    label: str


def is_active_weekday(d: date, mask: int) -> bool:
    bit = 1 << d.weekday()
    return bool(mask & bit)


def week_bounds(d: date, week_starts_on: int = DEFAULT_WEEK_START) -> tuple[date, date]:
    """Inclusive [start, end] for the week containing d."""
    ws = int(week_starts_on) % 7
    # offset from week start
    delta = (d.weekday() - ws) % 7
    start = d - timedelta(days=delta)
    end = start + timedelta(days=6)
    return start, end


def month_bounds(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    if d.month == 12:
        end = date(d.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(d.year, d.month + 1, 1) - timedelta(days=1)
    return start, end


def period_window(
    period: str,
    as_of: date,
    *,
    week_starts_on: int = DEFAULT_WEEK_START,
    active_weekdays: int = DEFAULT_WORKDAYS,
) -> PeriodWindow | None:
    """Current period window for a budget rule. Daily returns None if as_of not a workday."""
    p = (period or "monthly").lower()
    if p == "daily":
        if not is_active_weekday(as_of, active_weekdays):
            return None
        return PeriodWindow("daily", as_of, as_of, as_of.isoformat())
    if p == "weekly":
        s, e = week_bounds(as_of, week_starts_on)
        return PeriodWindow("weekly", s, e, f"{s.isoformat()}…{e.isoformat()}")
    if p in ("biweekly", "bi-weekly", "fortnight"):
        s, e = week_bounds(as_of, week_starts_on)
        s = s - timedelta(days=7)
        return PeriodWindow("biweekly", s, e, f"{s.isoformat()}…{e.isoformat()}")
    if p in ("yearly", "annual", "annually"):
        s = date(as_of.year, 1, 1)
        e = date(as_of.year, 12, 31)
        return PeriodWindow("yearly", s, e, str(as_of.year))
    # monthly
    s, e = month_bounds(as_of)
    return PeriodWindow("monthly", s, e, s.strftime("%Y-%m"))


def iter_active_days(start: date, end: date, active_weekdays: int) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        if is_active_weekday(cur, active_weekdays):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def next_workdays(as_of: date, n: int, active_weekdays: int) -> list[date]:
    """Next n active workdays starting at as_of (inclusive if workday)."""
    out: list[date] = []
    cur = as_of
    guard = 0
    while len(out) < n and guard < 400:
        if is_active_weekday(cur, active_weekdays):
            out.append(cur)
        cur += timedelta(days=1)
        guard += 1
    return out
