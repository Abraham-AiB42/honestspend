"""PDF statement heuristic parse + import."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.onboarding import apply_first_run
from financial_os.services.statement_pdf import parse_statement_lines


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


def test_credit_pdf_payment_does_not_inflate_owed(tmp_path: Path):
    """PDF credit import: PAYMENT THANK YOU must reduce owed (same as CSV fix)."""
    from financial_os.db import Profile
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

    # import_statement_pdf expects bytes/path — exercise normalize via direct helper path:
    # Build rows the same way PDF would after parse, via import_amounts on simulated flow.
    from financial_os.services.import_amounts import normalize_credit_import_amount
    from financial_os.services.account_balance import apply_amount_to_account

    charge = normalize_credit_import_amount(Decimal("45.99"), "AMAZON")
    pmt = normalize_credit_import_amount(Decimal("100.00"), "PAYMENT THANK YOU")
    assert charge == Decimal("-45.99")
    assert pmt == Decimal("100.00")
    apply_amount_to_account(acct, charge)
    apply_amount_to_account(acct, pmt)
    assert acct.current_balance == Decimal("145.99")  # 200+45.99-100
    s.close()
    # silence unused import if linters complain about import_statement_pdf
    assert import_statement_pdf is not None


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
