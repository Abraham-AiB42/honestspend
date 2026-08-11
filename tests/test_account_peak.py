"""Peak books balance: credit charges raise peak; void excluded."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Profile, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.account_peak import peak_balance


def _session(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_credit_charges_raise_peak(tmp_path: Path, monkeypatch):
    """Charges increase books balance; peak is high-water mark in lookback."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Peak Visa",
        current_balance=Decimal("0"),
        credit_limit=Decimal("5000"),
        statement_close_day=1,
    )
    s.add(card)
    s.flush()

    as_of = date(2026, 8, 15)
    # charge -100 → books +100; charge -50 → books 150; payment +40 → books 110
    s.add_all(
        [
            Transaction(
                profile_id=p.id,
                account_id=card.id,
                txn_date=date(2026, 8, 2),
                amount=Decimal("-100.00"),
                payee="Store A",
                status="cleared",
            ),
            Transaction(
                profile_id=p.id,
                account_id=card.id,
                txn_date=date(2026, 8, 5),
                amount=Decimal("-50.00"),
                payee="Store B",
                status="cleared",
            ),
            Transaction(
                profile_id=p.id,
                account_id=card.id,
                txn_date=date(2026, 8, 10),
                amount=Decimal("40.00"),
                payee="Payment",
                status="cleared",
            ),
        ]
    )
    s.commit()

    out = peak_balance(s, card.id, as_of=as_of, lookback_days=90)
    assert out["current"] == Decimal("110.00")
    assert out["peak_lookback"] == Decimal("150.00")
    assert out["peak_lookback_date"] == date(2026, 8, 5)
    assert out["lookback_days"] == 90
    # Open cycle from last close (2026-08-01) — all activity is after close
    assert out["peak_open_cycle"] == Decimal("150.00")
    s.close()


def test_void_excluded_from_peak(tmp_path: Path, monkeypatch):
    """Void (and pending) rows do not affect peak or current books walk."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Void Visa",
        current_balance=Decimal("0"),
        credit_limit=Decimal("5000"),
        statement_close_day=1,
    )
    s.add(card)
    s.flush()

    as_of = date(2026, 8, 20)
    s.add_all(
        [
            Transaction(
                profile_id=p.id,
                account_id=card.id,
                txn_date=date(2026, 8, 3),
                amount=Decimal("-100.00"),
                payee="Real charge",
                status="cleared",
            ),
            Transaction(
                profile_id=p.id,
                account_id=card.id,
                txn_date=date(2026, 8, 4),
                amount=Decimal("-500.00"),
                payee="Voided charge",
                status="void",
            ),
            Transaction(
                profile_id=p.id,
                account_id=card.id,
                txn_date=date(2026, 8, 5),
                amount=Decimal("-200.00"),
                payee="Pending charge",
                status="pending",
            ),
        ]
    )
    s.commit()

    out = peak_balance(s, card.id, as_of=as_of, lookback_days=90)
    assert out["current"] == Decimal("100.00")
    assert out["peak_lookback"] == Decimal("100.00")
    assert out["peak_lookback_date"] == date(2026, 8, 3)
    assert out["peak_open_cycle"] == Decimal("100.00")
    s.close()
