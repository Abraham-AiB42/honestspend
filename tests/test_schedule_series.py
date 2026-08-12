"""Versioned schedule series: rent steps, list, expand by window."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.db import Profile, init_db
from honestspend.engine.schedule_expand import expand_scheduled
from honestspend.seed import seed_all
from honestspend.services.schedule_series import (
    add_series_step,
    create_schedule_segment,
    list_series,
    new_series_id,
)


def _session(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'series.db').as_posix()}")
    init_db(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_new_series_id_unique():
    a = new_series_id()
    b = new_series_id()
    assert isinstance(a, str) and len(a) >= 16
    assert a != b


def test_rent_two_segments_expand_by_window(tmp_path: Path):
    """Two rent amounts by date: expand mid-first → A; mid-second → B."""
    s = _session(tmp_path)
    try:
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        amount_a = Decimal("-2000.00")
        amount_b = Decimal("-2100.00")

        first = create_schedule_segment(
            s,
            profile_id=p.id,
            series_label="Office rent",
            amount=amount_a,
            next_date=date(2026, 5, 1),
            cadence="monthly",
            day_of_month=1,
            start_date=date(2026, 4, 16),
            end_date=None,
            vendor="Acme Properties",
            opex_class="fixed",
            kind="expense",
        )
        s.commit()
        assert first.series_id
        assert first.amount == amount_a
        assert first.end_date is None

        second = add_series_step(
            s,
            first.series_id,
            new_amount=amount_b,
            effective_from=date(2026, 7, 29),
        )
        s.commit()

        # Previous segment closed day before step
        s.refresh(first)
        assert first.end_date == date(2026, 7, 28)
        assert second.series_id == first.series_id
        assert second.series_label == "Office rent"
        assert second.amount == amount_b
        assert second.start_date == date(2026, 7, 29)
        assert second.vendor == "Acme Properties"
        assert second.opex_class == "fixed"

        # Expand mid-first window (June) → amount A only from first segment
        mid_first_as_of = date(2026, 6, 15)
        horizon = mid_first_as_of + timedelta(days=45)
        occ_a = expand_scheduled(
            item_id=first.id,
            name=first.name,
            amount=first.amount,
            next_date=first.next_date,
            cadence=first.cadence,
            certainty=first.certainty,
            as_of=mid_first_as_of,
            horizon_end=horizon,
            start_date=first.start_date,
            end_date=first.end_date,
        )
        occ_b_early = expand_scheduled(
            item_id=second.id,
            name=second.name,
            amount=second.amount,
            next_date=second.next_date,
            cadence=second.cadence,
            certainty=second.certainty,
            as_of=mid_first_as_of,
            horizon_end=horizon,
            start_date=second.start_date,
            end_date=second.end_date,
        )
        assert occ_a, "expected occurrences in first segment window"
        assert all(o.amount == amount_a for o in occ_a)
        assert date(2026, 7, 1) in [o.on_date for o in occ_a]
        # Second segment should not fire before its start
        assert all(o.on_date >= date(2026, 7, 29) for o in occ_b_early)
        assert not any(o.on_date <= date(2026, 7, 28) for o in occ_b_early)

        # Expand mid-second window (August) → amount B from second; first done
        mid_second_as_of = date(2026, 8, 1)
        horizon2 = mid_second_as_of + timedelta(days=60)
        occ_a_late = expand_scheduled(
            item_id=first.id,
            name=first.name,
            amount=first.amount,
            next_date=first.next_date,
            cadence=first.cadence,
            certainty=first.certainty,
            as_of=mid_second_as_of,
            horizon_end=horizon2,
            start_date=first.start_date,
            end_date=first.end_date,
        )
        occ_b = expand_scheduled(
            item_id=second.id,
            name=second.name,
            amount=second.amount,
            next_date=second.next_date,
            cadence=second.cadence,
            certainty=second.certainty,
            as_of=mid_second_as_of,
            horizon_end=horizon2,
            start_date=second.start_date,
            end_date=second.end_date,
        )
        assert occ_a_late == []  # first segment ended 2026-07-28
        assert occ_b, "expected occurrences in second segment window"
        assert all(o.amount == amount_b for o in occ_b)
        assert date(2026, 8, 1) in [o.on_date for o in occ_b]
    finally:
        s.close()


def test_list_series_groups_segments(tmp_path: Path):
    s = _session(tmp_path)
    try:
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        a = create_schedule_segment(
            s,
            profile_id=p.id,
            series_label="Office rent",
            amount=Decimal("-2000"),
            next_date=date(2026, 5, 1),
            start_date=date(2026, 4, 16),
        )
        add_series_step(
            s,
            a.series_id,
            new_amount=Decimal("-2100"),
            effective_from=date(2026, 7, 29),
        )
        create_schedule_segment(
            s,
            profile_id=p.id,
            series_label="Internet",
            amount=Decimal("-80"),
            next_date=date(2026, 8, 5),
            vendor="ISP Co",
        )
        s.commit()

        series = list_series(s, p.id)
        by_label = {x["label"]: x for x in series}
        assert "Office rent" in by_label
        assert "Internet" in by_label
        rent = by_label["Office rent"]
        assert rent["series_id"] == a.series_id
        assert len(rent["segments"]) == 2
        amts = [Decimal(str(seg["amount"])) for seg in rent["segments"]]
        assert Decimal("-2000") in amts or Decimal("-2000.00") in amts
        assert any(Decimal(str(x)) == Decimal("-2100") for x in amts)
        assert rent["segments"][0]["end"] == date(2026, 7, 28)
        assert rent["segments"][1]["start"] == date(2026, 7, 29)
        assert len(by_label["Internet"]["segments"]) == 1
    finally:
        s.close()
