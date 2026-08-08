"""Fee keyword scanner."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, Profile, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.fee_scan import scan_fees


def test_scan_detects_late_fee(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'f.db').as_posix()}")
    init_db(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    seed_all(s)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    s.add(
        Transaction(
            profile_id=personal.id,
            account_id=acct.id,
            txn_date=date.today() - timedelta(days=3),
            amount=Decimal("-35"),
            payee="CARD LATE FEE",
            memo="",
            status="cleared",
            is_transfer=False,
        )
    )
    s.add(
        Transaction(
            profile_id=personal.id,
            account_id=acct.id,
            txn_date=date.today() - timedelta(days=2),
            amount=Decimal("-12.50"),
            payee="COFFEE SHOP",
            memo="",
            status="cleared",
            is_transfer=False,
        )
    )
    s.commit()

    out = scan_fees(s, days=30)
    assert out["count"] >= 1
    assert any("LATE FEE" in c["payee"].upper() for c in out["candidates"])
    assert Decimal(out["total_abs"]) >= Decimal("35")
    s.close()
