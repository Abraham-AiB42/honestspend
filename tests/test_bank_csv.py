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
    assert result.next_steps
    assert any(st.get("action") in ("review", "hold", "home") for st in result.next_steps)
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
    assert result2.next_steps  # still guides user (duplicates / hold)
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


def test_csv_ending_balance_column(tmp_path: Path):
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
    csv_text = """Date,Description,Amount,Balance
2026-08-01,COFFEE,-4.50,995.50
2026-08-02,PAY,-20.00,975.50
"""
    from financial_os.services.bank_csv import preview_bank_csv
    from financial_os.services.reconcile import trust_balance

    prev = preview_bank_csv(StringIO(csv_text))
    assert prev.get("ending_balance") == "975.50"
    assert prev["mapping"].get("balance_col")

    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
    )
    assert result.transactions_created == 2
    assert result.institution_balance_set is True
    assert result.ending_balance == "975.50"
    assert result.balance_source == "column"
    assert result.drift is not None
    s.refresh(acct)
    # Deltas applied: 1000 - 4.50 - 20 = 975.50; matches bank ending
    assert acct.current_balance == Decimal("975.50")
    assert acct.institution_balance == Decimal("975.50")
    # next_steps prefer honest path
    actions = {st.get("action") for st in result.next_steps}
    assert "hold" in actions or "set_books_from_bank" in actions or "review" in actions
    s.close()


def test_csv_import_updates_safe_to_spend_via_trust(tmp_path: Path):
    """Opening books wrong vs bank ending → trust institution fixes IFPP input."""
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Chase",
        current_balance=Decimal("500"),  # wrong open vs bank history
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    csv_text = """Date,Description,Amount,Balance
2026-08-01,COFFEE,-4.50,995.50
"""
    from financial_os.services.reconcile import trust_balance

    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
    )
    s.refresh(acct)
    # 500 - 4.50 = 495.50 books; bank says 995.50
    assert acct.current_balance == Decimal("495.50")
    assert acct.institution_balance == Decimal("995.50")
    assert any(st.get("action") == "set_books_from_bank" for st in result.next_steps)
    trust_balance(s, acct.id, trust="institution")
    s.refresh(acct)
    assert acct.current_balance == Decimal("995.50")
    s.close()


def test_csv_txn_id_dedupe(tmp_path: Path):
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
    csv_text = """Date,Description,Amount,Transaction ID
2026-08-01,COFFEE,-4.50,TXN-100
2026-08-02,MARKET,-10.00,TXN-101
"""
    r1 = import_bank_csv(s, account_id=acct.id, file_obj=StringIO(csv_text), auto_categorize=False)
    assert r1.transactions_created == 2
    # Same IDs, different payee text — still dedupe
    csv_text2 = """Date,Description,Amount,Transaction ID
2026-08-01,COFFEE SHOP #9,-4.50,TXN-100
2026-08-02,MARKET 42,-10.00,TXN-101
"""
    r2 = import_bank_csv(s, account_id=acct.id, file_obj=StringIO(csv_text2), auto_categorize=False)
    assert r2.transactions_created == 0
    assert r2.skipped_existing == 2
    from financial_os.db import Transaction

    assert s.query(Transaction).count() == 2
    s.close()


def test_csv_institution_balance_override(tmp_path: Path):
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Chase",
        current_balance=Decimal("500"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    csv_text = """Date,Description,Amount
2026-08-01,COFFEE,-4.50
"""
    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
        institution_balance=Decimal("480.00"),
    )
    assert result.institution_balance_set is True
    assert result.ending_balance == "480.00"
    assert result.balance_source == "override"
    s.refresh(acct)
    assert acct.institution_balance == Decimal("480.00")
    s.close()
