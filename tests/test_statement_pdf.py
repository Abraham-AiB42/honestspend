"""PDF statement heuristic parse + import."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.onboarding import apply_first_run
from financial_os.services.statement_pdf import (
    parse_statement_ending_balance,
    parse_statement_lines,
)


def test_parse_statement_lines_basic():
    text = """
    Account Activity 2026
    08/01/2026 COFFEE SHOP DOWNTOWN          -$4.50
    08/02/2026 WHOLE FOODS MARKET           -$62.10
    08/03/2026 PAYMENT THANK YOU             $200.00
    Previous Balance $100.00
    Minimum Payment Due $25.00
    """
    rows = parse_statement_lines(text)
    assert len(rows) >= 2
    payees = " ".join(r["payee"].lower() for r in rows)
    assert "coffee" in payees or "whole" in payees or "payment" in payees
    # amounts parsed
    assert all(isinstance(r["amount"], Decimal) for r in rows)


def test_parse_skips_headers():
    text = "Previous Balance $1,234.56\nCredit Limit $5,000.00\n"
    rows = parse_statement_lines(text)
    assert rows == []


def test_parse_statement_ending_balance_prefers_new():
    text = """
    Previous Balance $1,000.00
    Minimum Payment Due $35.00
    Ending Balance $850.00
    New Balance $842.15
    Credit Limit $5,000.00
    """
    bal, src = parse_statement_ending_balance(text)
    assert bal == Decimal("842.15")
    assert src == "new_balance"
    # Previous must not win
    bal2, _ = parse_statement_ending_balance("Previous Balance $9,999.00\n")
    assert bal2 is None


def test_parse_statement_ending_balance_keeps_negative_sign():
    """Overdraft / credit overpayment must not be abs()'d into positive bank truth."""
    bal, src = parse_statement_ending_balance("New Balance -$50.00\n")
    assert bal == Decimal("-50.00")
    assert src == "new_balance"
    bal2, _ = parse_statement_ending_balance("New Balance ($25.00)\n")
    assert bal2 == Decimal("-25.00")


def test_parse_bare_payment_line():
    """Bare PAYMENT payee must not be dropped as a pure total."""
    text = """
    Account Activity 2026
    08/01/2026 AMAZON                        $45.99
    08/02/2026 PAYMENT                       $100.00
    """
    rows = parse_statement_lines(text)
    payees = [r["payee"].upper() for r in rows]
    assert any("AMAZON" in p for p in payees)
    assert any(p.strip() == "PAYMENT" or p.startswith("PAYMENT") for p in payees)


def test_parse_keeps_dated_interest_charged():
    """Dated INTEREST CHARGED rows must import (not dropped as header)."""
    text = """
    Account Activity 2026
    08/01/2026 AMAZON                        $45.99
    08/15/2026 INTEREST CHARGED              $12.50
    Interest Charged $99.00
    """
    rows = parse_statement_lines(text)
    payees = " ".join(r["payee"].upper() for r in rows)
    assert "INTEREST" in payees
    assert any(
        abs(float(r["amount"])) == 12.5 and "INTEREST" in r["payee"].upper() for r in rows
    )


def test_credit_pdf_import_payment_reduces_owed(tmp_path: Path, monkeypatch):
    """End-to-end PDF credit import: charge + bare PAYMENT → owed falls."""
    from financial_os.db import Profile
    from financial_os.services import statement_pdf
    from financial_os.services.statement_pdf import import_statement_pdf

    eng = create_engine(f"sqlite:///{(tmp_path / 'pdf.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    seed_all(s)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="credit",
        nickname="Card",
        current_balance=Decimal("200"),
        credit_limit=Decimal("1000"),
        is_cash_for_ifpp=False,
    )
    s.add(acct)
    s.flush()

    text = """Account Activity 2026
08/01/2026 AMAZON                        $45.99
08/02/2026 PAYMENT                       $100.00
"""
    monkeypatch.setattr(
        statement_pdf,
        "extract_pdf_text",
        lambda _fo: (text, 1),
    )
    result = import_statement_pdf(
        s,
        account_id=acct.id,
        file_obj=b"%PDF-fake",
        filename="stmt.pdf",
        auto_categorize=False,
        amount_sign="bank",
    )
    assert result.transactions_created == 2
    by_payee = {t.payee.upper(): float(t.amount) for t in s.query(Transaction).all()}
    assert any("AMAZON" in k for k in by_payee)
    assert any(k.strip() == "PAYMENT" or k.startswith("PAYMENT") for k in by_payee)
    assert any(v == 100.0 for v in by_payee.values())
    s.refresh(acct)
    # 200 + 45.99 - 100 = 145.99
    assert acct.current_balance == Decimal("145.99")
    s.close()


def test_pdf_import_sets_institution_from_new_balance(tmp_path: Path, monkeypatch):
    """New Balance line → institution_balance + set_books next_step when drifted."""
    from financial_os.db import Profile
    from financial_os.services import statement_pdf
    from financial_os.services.statement_pdf import import_statement_pdf

    eng = create_engine(f"sqlite:///{(tmp_path / 'pdf2.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
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

    text = """Account Activity 2026
08/01/2026 COFFEE SHOP                   -$4.50
New Balance $995.50
"""
    monkeypatch.setattr(statement_pdf, "extract_pdf_text", lambda _fo: (text, 1))
    result = import_statement_pdf(
        s,
        account_id=acct.id,
        file_obj=b"%PDF-fake",
        filename="stmt.pdf",
        auto_categorize=False,
        amount_sign="bank",
    )
    assert result.transactions_created == 1
    assert result.institution_balance_set is True
    assert result.ending_balance == "995.50"
    assert result.balance_source == "new_balance"
    s.refresh(acct)
    assert acct.institution_balance == Decimal("995.50")
    # books after -$4.50 import = 995.50 → drift ~0
    assert result.drift is not None
    assert abs(Decimal(result.drift)) < Decimal("0.01")
    s.close()


def test_pdf_import_new_balance_with_drift_next_step(tmp_path: Path, monkeypatch):
    from financial_os.db import Profile
    from financial_os.services import statement_pdf
    from financial_os.services.statement_pdf import import_statement_pdf

    eng = create_engine(f"sqlite:///{(tmp_path / 'pdf3.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    seed_all(s)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("500"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    # No matching txn amount; New Balance still anchors bank truth
    text = """Account Activity 2026
08/01/2026 MYSTERY                       -$10.00
New Balance $900.00
"""
    monkeypatch.setattr(statement_pdf, "extract_pdf_text", lambda _fo: (text, 1))
    result = import_statement_pdf(
        s,
        account_id=acct.id,
        file_obj=b"%PDF-fake",
        filename="stmt.pdf",
        auto_categorize=False,
    )
    assert result.institution_balance_set is True
    s.refresh(acct)
    assert acct.institution_balance == Decimal("900.00")
    # books 500-10=490 vs bank 900 → set_books CTA
    assert any(st.get("action") == "set_books_from_bank" for st in result.next_steps)
    s.close()


def test_import_via_csv_inbox_still_works(tmp_path: Path, monkeypatch):
    """Regression: inbox path unchanged for CSV alongside PDF support."""
    from financial_os.services.import_inbox import ensure_inbox_layout, process_inbox

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 'financial_os.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("500"))
    s.commit()
    layout = ensure_inbox_layout()
    path = Path(layout["inbox"]) / "Primary-checking.csv"
    path.write_text(
        "Date,Description,Amount\n2026-08-01,PDF PATH TEST,-9.99\n",
        encoding="utf-8",
    )
    result = process_inbox(s)
    s.commit()
    assert result["transactions_created"] >= 1
    n = s.query(Transaction).count()
    assert n >= 1
    s.close()
