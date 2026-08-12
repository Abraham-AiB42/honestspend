"""Pending reserve recomputes card float (strict mode)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, AppSettings, Profile, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.ifpp_service import run_ifpp


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


def test_pending_reduces_card_float(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    row = s.get(AppSettings, 1)
    row.safety_buffer = Decimal("0")
    row.ifpp_cleared_only = True
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("0"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("5000"),
        payment_due_day=15,
        statement_close_day=1,
        promo_apr=Decimal("0"),
        promo_end_date=date.today() + timedelta(days=200),
    )
    s.add_all([cash, card])
    s.flush()
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=cash.id,
            txn_date=date.today(),
            amount=Decimal("-400"),
            payee="Pending big",
            status="pending",
            is_transfer=False,
        )
    )
    s.commit()
    r = run_ifpp(s, profile_id=p.id)
    assert r.cash_spendable == Decimal("600.00")
    assert r.details.get("card_float_after_pending") is True
    # Float must not still assume full $1000 cash
    assert r.card_float_interest_free <= Decimal("600.01")
    s.close()
