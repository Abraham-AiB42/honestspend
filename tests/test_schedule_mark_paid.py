"""Mark scheduled expense paid: advance cadence, optional txn, books move."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import (
    Account,
    Profile,
    ScheduledItem,
    Transaction,
    init_db,
    make_engine,
    make_session_factory,
)
from financial_os.seed import seed_all
from financial_os.services.schedule_mark_paid import mark_schedule_paid


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def _expense_setup(s, *, next_date: date, amount: Decimal = Decimal("-49.99"), end_date=None):
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Mark-paid cash",
        current_balance=Decimal("1000.00"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    item = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Streaming",
        amount=amount,
        next_date=next_date,
        end_date=end_date,
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
    )
    s.add(item)
    s.flush()
    return p, cash, item


def test_monthly_bill_advances(tmp_path: Path):
    s = _session(tmp_path)
    _, cash, item = _expense_setup(s, next_date=date(2026, 3, 15))
    bal_before = Decimal(str(cash.current_balance))

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 3, 15))
    s.commit()

    assert out["ok"] is True
    assert out["ended"] is False
    assert out["next_date"] == "2026-04-15"
    assert out.get("transaction_id") is not None

    s.refresh(item)
    s.refresh(cash)
    assert item.next_date == date(2026, 4, 15)
    assert item.active is True

    txn = s.get(Transaction, out["transaction_id"])
    assert txn is not None
    assert txn.status == "cleared"
    assert txn.amount == Decimal("-49.99")
    assert txn.account_id == cash.id
    assert txn.txn_date == date(2026, 3, 15)
    assert Decimal(str(cash.current_balance)) == bal_before - Decimal("49.99")
    s.close()


def test_creates_txn_and_books_move(tmp_path: Path):
    s = _session(tmp_path)
    _, cash, item = _expense_setup(s, next_date=date(2026, 8, 1), amount=Decimal("-120.00"))

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 8, 1), create_transaction=True)
    s.commit()

    assert out["transaction_id"]
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("880.00")
    txns = s.query(Transaction).filter(Transaction.account_id == cash.id).all()
    assert len(txns) == 1
    assert txns[0].payee == "Streaming"
    s.close()


def test_skip_transaction(tmp_path: Path):
    s = _session(tmp_path)
    _, cash, item = _expense_setup(s, next_date=date(2026, 5, 10))
    bal_before = Decimal(str(cash.current_balance))

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 5, 10), create_transaction=False)
    s.commit()

    assert "transaction_id" not in out
    s.refresh(item)
    s.refresh(cash)
    assert item.next_date == date(2026, 6, 10)
    assert Decimal(str(cash.current_balance)) == bal_before
    assert s.query(Transaction).count() == 0
    s.close()


def test_overdue_advances_from_as_of(tmp_path: Path):
    s = _session(tmp_path)
    _, _, item = _expense_setup(s, next_date=date(2026, 1, 5))

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 3, 20), create_transaction=False)
    s.commit()

    # base becomes as_of (next < as_of), then next monthly → 2026-04-20
    assert out["next_date"] == "2026-04-20"
    s.close()


def test_paid_through_end(tmp_path: Path):
    s = _session(tmp_path)
    _, _, item = _expense_setup(
        s,
        next_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 9, 1), create_transaction=False)
    s.commit()

    assert out["ended"] is True
    s.refresh(item)
    assert item.active is False
    assert item.ended_reason == "paid through end"
    assert item.next_date == date(2026, 10, 1)
    s.close()


def test_income_rejected(tmp_path: Path):
    s = _session(tmp_path)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    item = ScheduledItem(
        profile_id=p.id,
        name="Paycheck",
        amount=Decimal("2000"),
        next_date=date(2026, 4, 1),
        cadence="biweekly",
        certainty="fixed",
        kind="income",
        active=True,
    )
    s.add(item)
    s.flush()

    with pytest.raises(ValueError, match="expense"):
        mark_schedule_paid(s, item.id)
    s.close()


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "require_api_key", False)
    monkeypatch.setattr(settings, "allow_non_loopback", False)

    import financial_os.api.app as app_mod

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as s:
        seed_all(s)
        s.commit()

    with TestClient(app_mod.app) as c:
        yield c


def test_api_mark_paid(api_client: TestClient):
    import financial_os.api.app as app_mod

    with app_mod.SessionLocal() as s:
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        cash = Account(
            profile_id=p.id,
            kind="checking",
            nickname="API cash",
            current_balance=Decimal("500.00"),
            is_cash_for_ifpp=True,
        )
        s.add(cash)
        s.flush()
        item = ScheduledItem(
            profile_id=p.id,
            account_id=cash.id,
            name="Gym",
            amount=Decimal("-30.00"),
            next_date=date(2026, 6, 1),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            active=True,
        )
        s.add(item)
        s.commit()
        sid = item.id
        cash_id = cash.id

    r = api_client.post(f"/api/scheduled/{sid}/mark-paid", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["next_date"] == "2026-07-01"
    assert body.get("transaction_id")

    r2 = api_client.post(
        f"/api/scheduled/{sid}/mark-paid",
        json={"create_transaction": False},
    )
    assert r2.status_code == 200
    assert r2.json().get("transaction_id") is None
    assert r2.json()["next_date"] == "2026-08-01"

    with app_mod.SessionLocal() as s:
        cash = s.get(Account, cash_id)
        assert Decimal(str(cash.current_balance)) == Decimal("470.00")
