"""Running balance rebuild / verify from book-affecting ledger (ledger integrity)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Profile, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.account_balance import apply_amount_to_account
from financial_os.services.account_ledger import (
    rebuild_running_balance,
    verify_running_balance,
)


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


def test_rebuild_running_balance_credit(tmp_path: Path, monkeypatch):
    """charge -100 (books +100 owed), payment +40 (books -40 owed) → balance 60."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("0"),
        credit_limit=Decimal("5000"),
    )
    s.add(card)
    s.flush()

    charge = Transaction(
        profile_id=p.id,
        account_id=card.id,
        txn_date=date(2026, 8, 1),
        amount=Decimal("-100.00"),
        payee="Store",
        status="cleared",
        is_transfer=False,
    )
    payment = Transaction(
        profile_id=p.id,
        account_id=card.id,
        txn_date=date(2026, 8, 5),
        amount=Decimal("40.00"),
        payee="Payment",
        status="cleared",
        is_transfer=False,
    )
    s.add_all([charge, payment])
    apply_amount_to_account(card, charge.amount)
    apply_amount_to_account(card, payment.amount)
    s.commit()
    s.refresh(card)
    assert Decimal(str(card.current_balance)) == Decimal("60.00")

    card.current_balance = Decimal("999")  # corrupt
    s.commit()

    rebuilt = rebuild_running_balance(s, card.id)
    s.commit()
    s.refresh(card)

    assert rebuilt == Decimal("60.00")
    assert Decimal(str(card.current_balance)) == Decimal("60.00")
    assert Decimal(str(card.available_credit)) == Decimal("4940.00")  # 5000 - 60
    s.close()


def test_rebuild_running_balance_cash(tmp_path: Path, monkeypatch):
    """Cash: positive amount increases balance; expenses decrease it."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("0"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()

    income = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 8, 1),
        amount=Decimal("1000.00"),
        payee="Paycheck",
        status="cleared",
    )
    expense = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 8, 2),
        amount=Decimal("-250.50"),
        payee="Bills",
        status="cleared",
    )
    s.add_all([income, expense])
    apply_amount_to_account(cash, income.amount)
    apply_amount_to_account(cash, expense.amount)
    s.commit()
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("749.50")

    cash.current_balance = Decimal("1")  # corrupt
    s.commit()

    assert rebuild_running_balance(s, cash.id) == Decimal("749.50")
    s.commit()
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("749.50")
    s.close()


def test_rebuild_excludes_void_transactions(tmp_path: Path, monkeypatch):
    """Voided rows must not contribute to rebuilt balance."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("0"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()

    keep = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 8, 1),
        amount=Decimal("500.00"),
        payee="Keep",
        status="cleared",
    )
    voided = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 8, 2),
        amount=Decimal("-100.00"),
        payee="Mistake",
        status="void",
    )
    s.add_all([keep, voided])
    s.commit()

    cash.current_balance = Decimal("999")
    s.commit()

    assert rebuild_running_balance(s, cash.id) == Decimal("500.00")
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("500.00")
    s.close()


def test_rebuild_excludes_pending_transactions(tmp_path: Path, monkeypatch):
    """Pending rows must not contribute to rebuilt balance (live apply parity)."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("0"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()

    keep = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 8, 1),
        amount=Decimal("500.00"),
        payee="Keep",
        status="cleared",
    )
    pending = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 8, 2),
        amount=Decimal("-75.00"),
        payee="Pending buy",
        status="pending",
    )
    # Case-insensitive exclusion
    pending_upper = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 8, 3),
        amount=Decimal("-25.00"),
        payee="Pending upper",
        status="PENDING",
    )
    s.add_all([keep, pending, pending_upper])
    s.commit()

    cash.current_balance = Decimal("999")
    s.commit()

    assert rebuild_running_balance(s, cash.id) == Decimal("500.00")
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("500.00")
    s.close()


def test_verify_running_balance(tmp_path: Path, monkeypatch):
    """verify returns books, rebuilt, delta, ok without requiring write-first."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="MC",
        current_balance=Decimal("0"),
        credit_limit=Decimal("2000"),
    )
    s.add(card)
    s.flush()
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=card.id,
            txn_date=date(2026, 8, 1),
            amount=Decimal("-80.00"),
            payee="Charge",
            status="cleared",
        )
    )
    apply_amount_to_account(card, Decimal("-80.00"))
    s.commit()
    s.refresh(card)

    ok = verify_running_balance(s, card.id)
    assert ok["ok"] is True
    assert ok["books"] == Decimal("80.00")
    assert ok["rebuilt"] == Decimal("80.00")
    assert ok["delta"] == Decimal("0")

    card.current_balance = Decimal("50.00")  # drift
    s.commit()

    bad = verify_running_balance(s, card.id)
    assert bad["ok"] is False
    assert bad["books"] == Decimal("50.00")
    assert bad["rebuilt"] == Decimal("80.00")
    assert bad["delta"] == Decimal("-30.00")  # books - rebuilt
    # verify must not write
    s.refresh(card)
    assert Decimal(str(card.current_balance)) == Decimal("50.00")
    s.close()
