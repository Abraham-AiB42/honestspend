from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.db import Account, Profile, ScheduledItem, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.bank_csv import import_bank_csv


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
    from honestspend.services.bank_csv import preview_bank_csv

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


def test_csv_ending_balance_newest_first(tmp_path: Path):
    """Retail banks often export newest-first — ending bal is max date, not last row."""
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
    # Newest first: last file row is oldest running bal (would wrongly be 1000)
    csv_text = """Date,Description,Amount,Balance
2026-08-05,PAY,-20.00,980.00
2026-08-01,COFFEE,-4.50,1000.00
"""
    from honestspend.services.bank_csv import preview_bank_csv

    prev = preview_bank_csv(StringIO(csv_text))
    assert prev.get("ending_balance") == "980.00"
    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
    )
    assert result.ending_balance == "980.00"
    s.refresh(acct)
    assert acct.institution_balance == Decimal("980.00")
    s.close()


def test_csv_ending_balance_same_day_newest_first(tmp_path: Path):
    """Same-calendar-day newest-first: first row is latest running bal."""
    from honestspend.services.bank_csv import ending_balance_from_pairs, preview_bank_csv
    from datetime import date as date_cls

    pairs = [
        (date_cls(2026, 8, 5), Decimal("980.00")),
        (date_cls(2026, 8, 5), Decimal("1000.00")),
    ]
    amts = [Decimal("-20.00"), Decimal("-4.50")]
    assert ending_balance_from_pairs(pairs, amts) == Decimal("980.00")

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
2026-08-05,PAY,-20.00,980.00
2026-08-05,COFFEE,-4.50,1000.00
"""
    prev = preview_bank_csv(StringIO(csv_text))
    assert prev.get("ending_balance") == "980.00"
    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
    )
    assert result.ending_balance == "980.00"
    s.refresh(acct)
    assert acct.institution_balance == Decimal("980.00")
    s.close()


def test_csv_ending_balance_same_day_chronological(tmp_path: Path):
    """Same-day chronological: last row is ending running bal (amount-consistent)."""
    from honestspend.services.bank_csv import ending_balance_from_pairs, preview_bank_csv
    from datetime import date as date_cls

    pairs = [
        (date_cls(2026, 8, 5), Decimal("995.50")),
        (date_cls(2026, 8, 5), Decimal("975.50")),
    ]
    amts = [Decimal("-4.50"), Decimal("-20.00")]
    assert ending_balance_from_pairs(pairs, amts) == Decimal("975.50")

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
2026-08-05,COFFEE,-4.50,995.50
2026-08-05,PAY,-20.00,975.50
"""
    prev = preview_bank_csv(StringIO(csv_text))
    assert prev.get("ending_balance") == "975.50"
    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
    )
    assert result.ending_balance == "975.50"
    s.refresh(acct)
    assert acct.institution_balance == Decimal("975.50")
    s.close()


def test_csv_ending_balance_max_day_slice_amount_orientation():
    """Multi-day file: amount orientation on max-day rows only (not whole-file same-day)."""
    from honestspend.services.bank_csv import ending_balance_from_pairs
    from datetime import date as date_cls

    # Newest-first multi-day; max day (08-05) has two rows: pay then older coffee same day
    pairs = [
        (date_cls(2026, 8, 5), Decimal("980.00")),
        (date_cls(2026, 8, 5), Decimal("1000.00")),
        (date_cls(2026, 8, 1), Decimal("1004.50")),
    ]
    amts = [Decimal("-20.00"), Decimal("-4.50"), Decimal("-10.00")]
    assert ending_balance_from_pairs(pairs, amts) == Decimal("980.00")

    # Chronological multi-day: max day last row is true ending
    pairs2 = [
        (date_cls(2026, 8, 1), Decimal("1000.00")),
        (date_cls(2026, 8, 5), Decimal("995.50")),
        (date_cls(2026, 8, 5), Decimal("975.50")),
    ]
    amts2 = [Decimal("-5.00"), Decimal("-4.50"), Decimal("-20.00")]
    assert ending_balance_from_pairs(pairs2, amts2) == Decimal("975.50")


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
    from honestspend.services.reconcile import trust_balance

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
    from honestspend.db import Transaction

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


def test_credit_csv_positive_charges_as_spend(tmp_path: Path):
    """Credit card CSVs often list charges as positive — store as negative spend (PDF parity)."""
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("200"),
        credit_limit=Decimal("1000"),
        is_cash_for_ifpp=False,
    )
    s.add(acct)
    s.flush()
    csv_text = """Date,Description,Amount
2026-08-01,AMAZON,45.99
2026-08-02,TARGET,12.00
"""
    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
        amount_sign="bank",
    )
    assert result.transactions_created == 2
    amts = sorted(float(t.amount) for t in s.query(Transaction).all())
    assert amts == [-45.99, -12.0]
    s.refresh(acct)
    # Credit: owed -= amount; charges negative → owed rises 200+45.99+12
    assert acct.current_balance == Decimal("257.99")
    assert acct.available_credit == Decimal("742.01")
    s.close()


def test_credit_csv_payment_does_not_inflate_owed(tmp_path: Path):
    """Payments (even as + or −) must reduce balance owed, not raise it."""
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("200"),
        credit_limit=Decimal("1000"),
        is_cash_for_ifpp=False,
    )
    s.add(acct)
    s.flush()
    # Issuer-style: charges +, payment − ; plus bank-style payment +
    csv_text = """Date,Description,Amount
2026-08-01,AMAZON,45.99
2026-08-02,PAYMENT THANK YOU,-100.00
2026-08-03,AUTOPAY PAYMENT,50.00
"""
    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
        amount_sign="bank",
    )
    assert result.transactions_created == 3
    by_payee = {t.payee: float(t.amount) for t in s.query(Transaction).all()}
    assert by_payee["AMAZON"] == -45.99
    assert by_payee["PAYMENT THANK YOU"] == 100.0
    assert by_payee["AUTOPAY PAYMENT"] == 50.0
    s.refresh(acct)
    # 200 + 45.99 - 100 - 50 = 95.99
    assert acct.current_balance == Decimal("95.99")
    s.close()


def test_credit_csv_refund_and_payment_balance(tmp_path: Path):
    """Refunds and payments both reduce owed; charges increase it."""
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("200"),
        credit_limit=Decimal("1000"),
        is_cash_for_ifpp=False,
    )
    s.add(acct)
    s.flush()
    csv_text = """Date,Description,Amount
2026-08-01,AMAZON,80.00
2026-08-02,AMAZON REFUND,20.00
2026-08-03,PAYMENT,50.00
"""
    result = import_bank_csv(
        s,
        account_id=acct.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
        amount_sign="bank",
    )
    assert result.transactions_created == 3
    by_payee = {t.payee: float(t.amount) for t in s.query(Transaction).all()}
    assert by_payee["AMAZON"] == -80.0
    assert by_payee["AMAZON REFUND"] == 20.0
    assert by_payee["PAYMENT"] == 50.0
    s.refresh(acct)
    # 200 + 80 - 20 - 50 = 210
    assert acct.current_balance == Decimal("210.00")
    s.close()


def test_csv_import_advances_matching_schedule(tmp_path: Path):
    """After bank CSV creates outflows, high-confidence schedules are advanced."""
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Cash",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    rent = ScheduledItem(
        profile_id=personal.id,
        account_id=cash.id,
        name="Rent",
        amount=Decimal("-1200.00"),
        next_date=date(2026, 6, 1),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
    )
    s.add(rent)
    s.flush()

    csv_text = """Date,Description,Amount
06/01/2026,Rent ACME LLC,-1200.00
"""
    result = import_bank_csv(
        s,
        account_id=cash.id,
        file_obj=StringIO(csv_text),
        auto_categorize=False,
    )
    assert result.transactions_created == 1
    assert result.schedule_advance_error is None
    assert result.schedules_advanced == 1
    assert "Rent" in (result.schedules_advanced_names or [])
    s.refresh(rent)
    assert rent.next_date == date(2026, 7, 1)
    s.close()
