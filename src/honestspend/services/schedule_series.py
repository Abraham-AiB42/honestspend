"""Versioned schedule series: create segments, supersede (rent steps), list series."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from honestspend.db import ScheduledItem
from honestspend.engine.schedule_expand import next_occurrence

ZERO = Decimal("0")


def new_series_id() -> str:
    """Return a new series id (uuid4 hex)."""
    return uuid4().hex


def _align_day(d: date, day: int) -> date:
    last = monthrange(d.year, d.month)[1]
    return d.replace(day=min(max(1, day), last))


def _normalize_amount(amount: Decimal, kind: str) -> Decimal:
    amt = Decimal(str(amount))
    k = (kind or "expense").lower()
    if k in ("expense", "owner_draw"):
        return -abs(amt) if amt != ZERO else ZERO
    if k == "income":
        return abs(amt)
    return amt


def _first_next_on_or_after(
    *,
    on_or_after: date,
    cadence: str,
    day_of_month: int | None,
    template_next: date | None = None,
) -> date:
    """Pick next_date for a segment starting on_or_after."""
    if day_of_month is not None:
        candidate = _align_day(on_or_after, day_of_month)
        if candidate < on_or_after:
            # roll one period from aligned day in this month
            candidate = next_occurrence(candidate, cadence)
        return candidate
    if template_next is not None:
        candidate = _align_day(on_or_after, template_next.day)
        if candidate < on_or_after:
            candidate = next_occurrence(candidate, cadence)
        # Walk template cadence if still before window (weekly etc.)
        guard = 0
        cursor = template_next
        while cursor < on_or_after and guard < 500:
            cursor = next_occurrence(cursor, cadence)
            guard += 1
        if cadence in ("weekly", "biweekly"):
            return cursor
        return candidate
    return on_or_after


def create_schedule_segment(
    session: Session,
    *,
    profile_id: int,
    series_label: str,
    amount: Decimal,
    next_date: date,
    cadence: str = "monthly",
    day_of_month: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    account_id: int | None = None,
    vendor: str | None = None,
    opex_class: str | None = None,
    series_id: str | None = None,
    kind: str = "expense",
    certainty: str = "fixed",
    income_source: str | None = None,
    notes: str | None = None,
) -> ScheduledItem:
    """Create one schedule segment; assigns series_id if not provided."""
    sid = series_id or new_series_id()
    label = (series_label or "").strip() or "Schedule"
    nd = next_date
    if day_of_month is not None:
        nd = _align_day(nd, day_of_month)

    row = ScheduledItem(
        profile_id=profile_id,
        account_id=account_id,
        name=label[:128],
        amount=_normalize_amount(amount, kind),
        next_date=nd,
        start_date=start_date,
        end_date=end_date,
        cadence=cadence or "monthly",
        certainty=certainty or "fixed",
        kind=(kind or "expense").lower(),
        series_id=sid,
        series_label=label[:128],
        vendor=vendor,
        opex_class=opex_class,
        income_source=income_source,
        notes=notes,
        active=True,
    )
    session.add(row)
    session.flush()
    return row


def add_series_step(
    session: Session,
    series_id: str,
    *,
    new_amount: Decimal,
    effective_from: date,
    end_date: date | None = None,
    day_of_month: int | None = None,
    next_date: date | None = None,
) -> ScheduledItem:
    """Close previous open-ended segment and insert a new amount step.

    Previous open segment (end_date is None) gets end_date = effective_from - 1 day.
    New segment reuses series_id / label / account / vendor / cadence / kind / opex.
    """
    if not series_id:
        raise ValueError("series_id is required")

    rows = (
        session.query(ScheduledItem)
        .filter(ScheduledItem.series_id == series_id)
        .order_by(ScheduledItem.id.asc())
        .all()
    )
    if not rows:
        raise ValueError(f"No schedule segments for series_id={series_id!r}")

    # Prefer open-ended active segment; else latest segment with null/missing end or end >= effective
    open_seg: ScheduledItem | None = None
    for r in rows:
        if r.end_date is None:
            open_seg = r
            break
    if open_seg is None:
        # Fallback: segment whose window still covers or ends after step-1 day
        close_day = effective_from - timedelta(days=1)
        for r in reversed(rows):
            if r.end_date is None or r.end_date >= close_day:
                open_seg = r
                break
    if open_seg is None:
        open_seg = rows[-1]

    close_end = effective_from - timedelta(days=1)
    # Only close if currently open or ends on/after the step boundary
    if open_seg.end_date is None or open_seg.end_date >= effective_from:
        open_seg.end_date = close_end

    cadence = open_seg.cadence or "monthly"
    kind = open_seg.kind or "expense"
    dom = day_of_month
    if dom is None and open_seg.next_date is not None:
        # Keep bill day of month for monthly/semimonthly/yearly
        if cadence.lower() in ("monthly", "semimonthly", "yearly"):
            dom = open_seg.next_date.day

    if next_date is not None:
        nd = next_date
        if dom is not None:
            nd = _align_day(nd, dom)
    else:
        nd = _first_next_on_or_after(
            on_or_after=effective_from,
            cadence=cadence,
            day_of_month=dom,
            template_next=open_seg.next_date,
        )

    new_row = ScheduledItem(
        profile_id=open_seg.profile_id,
        account_id=open_seg.account_id,
        category_id=open_seg.category_id,
        name=open_seg.name,
        amount=_normalize_amount(new_amount, kind),
        next_date=nd,
        start_date=effective_from,
        end_date=end_date,
        cadence=cadence,
        certainty=open_seg.certainty or "fixed",
        kind=kind,
        series_id=series_id,
        series_label=open_seg.series_label or open_seg.name,
        vendor=open_seg.vendor,
        opex_class=open_seg.opex_class,
        income_source=open_seg.income_source,
        notes=open_seg.notes,
        active=True,
    )
    session.add(new_row)
    session.flush()
    return new_row


def list_series(session: Session, profile_id: int) -> list[dict]:
    """Group scheduled items by series_id for a profile.

    Returns:
      [{
        series_id, label, profile_id,
        segments: [{id, amount, start, end, next_date, active, name, vendor, ...}]
      }, ...]
    Items without series_id are omitted.
    """
    rows = (
        session.query(ScheduledItem)
        .filter(
            ScheduledItem.profile_id == profile_id,
            ScheduledItem.series_id.isnot(None),
        )
        .order_by(ScheduledItem.series_id.asc(), ScheduledItem.id.asc())
        .all()
    )
    by_sid: dict[str, list[ScheduledItem]] = {}
    for r in rows:
        sid = r.series_id
        if not sid:
            continue
        by_sid.setdefault(sid, []).append(r)

    out: list[dict] = []
    for sid, segs in by_sid.items():
        label = next(
            (s.series_label or s.name for s in segs if s.series_label or s.name),
            sid,
        )
        out.append(
            {
                "series_id": sid,
                "label": label,
                "profile_id": profile_id,
                "segments": [
                    {
                        "id": s.id,
                        "amount": s.amount,
                        "start": s.start_date,
                        "end": s.end_date,
                        "next_date": s.next_date,
                        "active": bool(s.active),
                        "name": s.name,
                        "vendor": s.vendor,
                        "opex_class": s.opex_class,
                        "kind": s.kind,
                        "cadence": s.cadence,
                        "account_id": s.account_id,
                    }
                    for s in segs
                ],
            }
        )
    # Stable order by label then series_id
    out.sort(key=lambda x: ((x.get("label") or "").lower(), x["series_id"]))
    return out
