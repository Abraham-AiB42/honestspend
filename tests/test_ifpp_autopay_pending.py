"""IFPP: skip card autopay double-count; strict pending reserve."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, AppSettings, Profile, ScheduledItem, Transaction, init_db
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


def test_card_autopay_not_double_counted_in_cash(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    row = s.get(AppSettings, 1)
    row.safety_buffer = Decimal("0")
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("400"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4600"),
        payment_due_day=15,
        statement_close_day=1,
    )
    s.add_all([cash, card])
    s.flush()
    # Autopay schedule that would steal $400 from cash runway
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Autopay · statement · Visa",
            amount=Decimal("-400"),
            next_date=date.today() + timedelta(days=5),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=card.id,
            notes="Autopay policy=statement",
            active=True,
        )
    )
    s.commit()
    r = run_ifpp(s, profile_id=p.id)
    assert r.details.get("skipped_card_autopay_schedules") == 1
    # Without skip, cash would drop by 400 toward red; with skip, ~2000
    assert r.cash_spendable >= Decimal("1500")
    s.close()


def test_cleared_only_reserves_pending(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    row = s.get(AppSettings, 1)
    row.safety_buffer = Decimal("0")
    row.ifpp_cleared_only = True
    acct = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=acct.id,
            txn_date=date.today(),
            amount=Decimal("-100"),
            payee="Pending store",
            status="pending",
            is_transfer=False,
        )
    )
    s.commit()
    r = run_ifpp(s, profile_id=p.id)
    assert r.details.get("ifpp_cleared_only") is True
    assert Decimal(r.details.get("pending_reserved") or "0") == Decimal("100.00")
    assert r.cash_spendable == Decimal("900.00")
    s.close()


def test_cleared_only_off_warning_only(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    row = s.get(AppSettings, 1)
    row.safety_buffer = Decimal("0")
    row.ifpp_cleared_only = False
    acct = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=acct.id,
            txn_date=date.today(),
            amount=Decimal("-100"),
            payee="Pending store",
            status="pending",
            is_transfer=False,
        )
    )
    s.commit()
    r = run_ifpp(s, profile_id=p.id)
    assert Decimal(r.details.get("pending_reserved") or "0") == Decimal("0")
    assert r.cash_spendable == Decimal("1000.00")
    assert r.details.get("pending_warning")
    s.close()
