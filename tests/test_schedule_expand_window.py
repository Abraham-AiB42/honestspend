"""expand_scheduled respects start_date window (and end_date)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from financial_os.engine.schedule_expand import expand_scheduled


def test_expand_skips_occurrences_before_start_date():
    """Occurrences before start_date excluded; on/after start_date included."""
    as_of = date(2026, 1, 1)
    occ = expand_scheduled(
        item_id=1,
        name="Owner draw",
        amount=Decimal("-2500"),
        next_date=date(2026, 1, 15),
        cadence="monthly",
        certainty="fixed",
        as_of=as_of,
        horizon_end=date(2026, 6, 30),
        start_date=date(2026, 3, 1),
    )
    dates = [o.on_date for o in occ]
    assert date(2026, 1, 15) not in dates
    assert date(2026, 2, 15) not in dates
    assert date(2026, 3, 15) in dates
    assert date(2026, 4, 15) in dates
    assert all(d >= date(2026, 3, 1) for d in dates)


def test_expand_start_date_none_behaves_as_before():
    """Without start_date, all occurrences from next_date within as_of..horizon."""
    as_of = date(2026, 1, 1)
    occ = expand_scheduled(
        item_id=1,
        name="Rent",
        amount=Decimal("-1100"),
        next_date=date(2026, 1, 1),
        cadence="monthly",
        certainty="fixed",
        as_of=as_of,
        horizon_end=date(2026, 3, 31),
        start_date=None,
    )
    dates = [o.on_date for o in occ]
    assert date(2026, 1, 1) in dates
    assert date(2026, 2, 1) in dates
    assert date(2026, 3, 1) in dates


def test_expand_start_and_end_date_window():
    """Both start_date and end_date bound the series."""
    as_of = date(2026, 1, 1)
    occ = expand_scheduled(
        item_id=1,
        name="Seasonal",
        amount=Decimal("-100"),
        next_date=date(2026, 1, 10),
        cadence="monthly",
        certainty="fixed",
        as_of=as_of,
        horizon_end=as_of + timedelta(days=365),
        start_date=date(2026, 3, 1),
        end_date=date(2026, 5, 15),
    )
    dates = [o.on_date for o in occ]
    assert dates == [date(2026, 3, 10), date(2026, 4, 10), date(2026, 5, 10)]


def test_expand_start_date_after_horizon_empty():
    as_of = date(2026, 1, 1)
    occ = expand_scheduled(
        item_id=1,
        name="Future",
        amount=Decimal("-50"),
        next_date=date(2026, 1, 1),
        cadence="monthly",
        certainty="fixed",
        as_of=as_of,
        horizon_end=date(2026, 2, 28),
        start_date=date(2026, 6, 1),
    )
    assert occ == []
