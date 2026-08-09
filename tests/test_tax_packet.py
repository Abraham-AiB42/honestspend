from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, Base, Category, Profile, Transaction
from financial_os.seed import seed_all
from financial_os.services.tax_packet import build_tax_packet, packet_to_csv_files


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    return s


def test_meals_50_percent_in_packet(tmp_path: Path):
    s = _session(tmp_path)
    from financial_os.services.profiles import create_profile

    personal = create_profile(s, display_name="Demo Business", entity_type="business")
    s.flush()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    meals = (
        s.query(Category)
        .filter(Category.profile_id == personal.id, Category.display_name == "Business meals")
        .one()
    )
    s.add(
        Transaction(
            profile_id=personal.id,
            account_id=acct.id,
            category_id=meals.id,
            txn_date=date(2026, 3, 1),
            amount=Decimal("-100.00"),
            payee="Client lunch",
        )
    )
    s.flush()
    packet = build_tax_packet(s, profile_id=personal.id, year=2026)
    lines = { (r["tax_form"], r["tax_line"]): r for r in packet["summary_by_tax_line"] }
    # meals roll to 1120S line 20
    row = lines[("1120S", "20")]
    assert Decimal(row["gross_amount"]) == Decimal("-100.00")
    assert Decimal(row["tax_relevant_amount"]) == Decimal("50.00")
    files = packet_to_csv_files(packet)
    assert "expenses_by_tax_line.csv" in files
    assert "meals_50" in files["expenses_by_tax_line.csv"]
    s.close()


def test_personal_nontax_still_in_transactions(tmp_path: Path):
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Primary checking",
        current_balance=Decimal("500"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    groceries = (
        s.query(Category)
        .filter(Category.code == "PER_GROCERIES")
        .one()
    )
    s.add(
        Transaction(
            profile_id=personal.id,
            account_id=acct.id,
            category_id=groceries.id,
            txn_date=date(2026, 1, 15),
            amount=Decimal("-42.50"),
            payee="Costco",
        )
    )
    s.flush()
    packet = build_tax_packet(s, profile_id=personal.id, year=2026)
    assert packet["counts"]["transactions"] == 1
    assert any(t["payee"] == "Costco" for t in packet["transactions"])
    s.close()
