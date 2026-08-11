"""Schema: schedule series, vendor, opex, income_source, owner_draw kind."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from financial_os.db import Profile, ScheduledItem, init_db
from financial_os.migrations import SCHEMA_VERSION, get_schema_version
from financial_os.seed import seed_all


def _engine(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'schema.db').as_posix()}")
    init_db(eng)
    return eng


def test_schema_version_and_agency_columns(tmp_path: Path):
    eng = _engine(tmp_path)
    assert get_schema_version(eng) == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 22

    for attr in (
        "start_date",
        "series_id",
        "series_label",
        "vendor",
        "opex_class",
        "income_source",
    ):
        assert hasattr(ScheduledItem, attr), f"ScheduledItem missing attr {attr}"

    cols = {c["name"] for c in inspect(eng).get_columns("scheduled_items")}
    for name in (
        "start_date",
        "series_id",
        "series_label",
        "vendor",
        "opex_class",
        "income_source",
    ):
        assert name in cols, f"scheduled_items missing column {name}"

    idx_names = {ix["name"] for ix in inspect(eng).get_indexes("scheduled_items")}
    assert "ix_scheduled_items_series" in idx_names


def test_owner_draw_series_roundtrip(tmp_path: Path):
    eng = _engine(tmp_path)
    Session = sessionmaker(bind=eng)
    s = Session()
    try:
        seed_all(s)
        s.commit()
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        series = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        item = ScheduledItem(
            profile_id=p.id,
            name="Owner draw",
            amount=Decimal("-2500.00"),
            next_date=date(2026, 8, 15),
            start_date=date(2026, 8, 1),
            cadence="monthly",
            certainty="fixed",
            kind="owner_draw",
            series_id=series,
            series_label="Owner draws 2026",
            vendor=None,
            opex_class=None,
            income_source=None,
            active=True,
        )
        s.add(item)
        s.commit()

        loaded = s.get(ScheduledItem, item.id)
        assert loaded is not None
        assert loaded.kind == "owner_draw"
        assert loaded.series_id == series
        assert loaded.series_label == "Owner draws 2026"
        assert loaded.start_date == date(2026, 8, 1)
        assert loaded.amount == Decimal("-2500.00")

        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT kind, series_id, start_date FROM scheduled_items WHERE id = :id"
                ),
                {"id": item.id},
            ).fetchone()
            assert row is not None
            assert row[0] == "owner_draw"
            assert row[1] == series
            assert str(row[2]).startswith("2026-08-01")
    finally:
        s.close()


def test_expense_vendor_opex_income_source_roundtrip(tmp_path: Path):
    eng = _engine(tmp_path)
    Session = sessionmaker(bind=eng)
    s = Session()
    try:
        seed_all(s)
        s.commit()
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        bill = ScheduledItem(
            profile_id=p.id,
            name="Office rent",
            amount=Decimal("-2100.00"),
            next_date=date(2026, 7, 29),
            start_date=date(2026, 7, 1),
            end_date=date(2027, 7, 30),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            series_id="rent-series-001",
            series_label="Office rent",
            vendor="Acme Properties",
            opex_class="fixed",
            active=True,
        )
        income = ScheduledItem(
            profile_id=p.id,
            name="Client retainers",
            amount=Decimal("8000.00"),
            next_date=date(2026, 8, 1),
            start_date=date(2026, 1, 1),
            cadence="monthly",
            certainty="expected",
            kind="income",
            income_source="retainers",
            active=True,
        )
        s.add_all([bill, income])
        s.commit()

        loaded_bill = s.get(ScheduledItem, bill.id)
        assert loaded_bill.vendor == "Acme Properties"
        assert loaded_bill.opex_class == "fixed"
        assert loaded_bill.series_id == "rent-series-001"

        loaded_inc = s.get(ScheduledItem, income.id)
        assert loaded_inc.income_source == "retainers"
        assert loaded_inc.kind == "income"
    finally:
        s.close()
