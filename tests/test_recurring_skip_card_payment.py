"""Recurring detect must not suggest credit-card payment payees as bills."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.recurring_detect import detect_recurring


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


def test_payment_to_card_not_suggested(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Capital One",
        institution="Capital One",
        current_balance=Decimal("800"),
        credit_limit=Decimal("5000"),
    )
    s.add_all([cash, card])
    s.flush()
    as_of = date.today()
    for i in range(3):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=as_of - timedelta(days=30 * (i + 1)),
                amount=Decimal("-200"),
                payee="Payment to Capital One",
                status="cleared",
                is_transfer=False,
            )
        )
    # Real subscription for control
    for i in range(3):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=as_of - timedelta(days=30 * (i + 1)),
                amount=Decimal("-15.99"),
                payee="Netflix.com",
                status="cleared",
                is_transfer=False,
            )
        )
    s.commit()

    det = detect_recurring(s, profile_id=p.id, as_of=as_of, min_occurrences=2, limit=20)
    names = " ".join((x.get("name") or "") + " " + (x.get("normalized") or "") for x in det.get("suggestions") or [])
    assert "capital" not in names.lower()
    assert "payment to" not in names.lower()
    # Netflix-style should still surface
    assert any("netflix" in (x.get("name") or "").lower() for x in det.get("suggestions") or [])
    s.close()
