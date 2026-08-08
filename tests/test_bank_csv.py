from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, Profile, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.bank_csv import import_bank_csv


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    return s


def test_import_simple_csv(tmp_path: Path):
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Chase",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()

    csv_text = """Date,Description,Amount
01/15/2026,SHELL OIL,-42.10
01/16/2026,PAYCHECK,2000.00
01/17/2026,NETFLIX.COM,-15.99
"""
    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        filename="chase.csv",
        auto_categorize=True,
    )
    assert result.transactions_created == 3
    assert not result.errors
    txns = s.query(Transaction).order_by(Transaction.txn_date).all()
    assert len(txns) == 3
    assert txns[0].payee == "SHELL OIL"
    # Shell should auto-categorize via rules
    assert result.categorized >= 1
    # idempotent
    result2 = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        filename="chase.csv",
        auto_categorize=False,
    )
    assert result2.transactions_created == 0
    assert result2.skipped_existing == 3
    s.close()


def test_debit_credit_columns(tmp_path: Path):
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="CU",
        current_balance=Decimal("100"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    csv_text = """Post Date,Payee,Debit,Credit
2026-02-01,STORE,25.00,
2026-02-02,DEPOSIT,,500.00
"""
    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
    )
    assert result.transactions_created == 2
    amts = sorted(float(t.amount) for t in s.query(Transaction).all())
    assert amts == [-25.0, 500.0]
    s.close()
