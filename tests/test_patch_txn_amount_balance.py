"""PATCH amount must rebalance books so Safe to spend stays honest."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.account_balance import (
    apply_amount_to_account,
    reverse_amount_on_account,
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


def test_amount_change_rebalances_like_patch(tmp_path: Path, monkeypatch):
    """Mirrors patch_transaction amount path without HTTP middleware."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("1000.00"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    row = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date.today(),
        amount=Decimal("-100.00"),
        payee="Coffee",
        status="cleared",
        is_transfer=False,
    )
    s.add(row)
    apply_amount_to_account(cash, row.amount)
    s.commit()
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("900.00")

    prev_amount = row.amount
    new_amount = Decimal("-50.00")
    reverse_amount_on_account(cash, prev_amount)
    row.amount = new_amount
    apply_amount_to_account(cash, new_amount)
    s.commit()
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("950.00")
    s.close()
