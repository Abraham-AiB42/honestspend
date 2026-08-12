"""Setup CSV cash-account loop."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, init_db
from honestspend.seed import seed_all
from honestspend.services.setup_cash import cash_accounts_status, create_cash_account


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


def test_create_checking_primary(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    res = create_cash_account(
        s,
        account_type="checking",
        nickname="Ops checking",
        bank_guide_id="chase",
        current_balance=Decimal("1200"),
    )
    s.commit()
    assert res["account"]["id"]
    assert res["account"]["is_cash_for_ifpp"] is True
    assert res["guide"]["id"] == "chase"
    assert "login_url" in res["guide"]
    st = cash_accounts_status(s)
    assert st["count"] == 1
    assert st["has_cash"] is True
    s.close()


def test_money_market_and_second_checking(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    create_cash_account(s, account_type="checking", nickname="Main")
    create_cash_account(s, account_type="money_market", nickname="MM", bank_guide_id="fidelity")
    create_cash_account(s, account_type="checking", nickname="Side")
    s.commit()
    accounts = s.query(Account).filter(Account.kind.in_(("checking", "savings"))).all()
    assert len(accounts) == 3
    primaries = [a for a in accounts if a.is_cash_for_ifpp]
    assert len(primaries) == 1
    assert primaries[0].nickname == "Main"
    mm = s.query(Account).filter(Account.nickname == "MM").one()
    assert mm.kind == "savings"
    s.close()


def test_invalid_type(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    try:
        create_cash_account(s, account_type="brokerage")
        ok = False
    except ValueError:
        ok = True
    assert ok
    s.close()
