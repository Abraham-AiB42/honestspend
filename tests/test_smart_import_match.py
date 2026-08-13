"""Loan matching + remap merge/undo."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.account_merge import merge_account_into, undo_account_merge
from honestspend.services.smart_import import build_smart_plan, guess_account_kind


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


def test_guess_loan_kind_from_transaction_details_filename():
    kind = guess_account_kind(
        filename="hyundai_motor_finance_transaction_details.xls",
        raw_text="Date,Description,Amount\n08/01/2026,REGULAR PAYMENT,-485.00\n",
        headers=["Date", "Description", "Amount"],
    )
    assert kind == "loan"


def test_discover_card_statement_stays_credit():
    from honestspend.services.smart_import import last4_from_text

    kind = guess_account_kind(
        filename="Discover-Statement-20260720-7990.pdf",
        raw_text="Discover it® Card  Account ending in 7990  New Balance",
    )
    assert kind == "credit"
    assert last4_from_text("Discover-Statement-20260728-3065.pdf") == "3065"
    assert (
        last4_from_text(
            "Discover-Statement-20260728-3065.pdf",
            "August 2026 statement page 1 of 6 prepared for\n" * 20,
        )
        == "3065"
    )


def test_discover_personal_loan_statement_is_loan():
    from honestspend.services.smart_import import guess_loan_label

    kind = guess_account_kind(
        filename="Discover-Statement-20260728-3065.pdf",
        raw_text="LOAN AGREEMENT: Please refer to the Truth-in-Lending Disclosures. "
        "Discover Personal Loans, PO Box 30396.",
    )
    assert kind == "loan"
    label, detail = guess_loan_label(
        "Discover-Statement-20260728-3065.pdf",
        "Discover Personal Loans  LOAN AGREEMENT",
    )
    assert label == "Personal loan"
    assert detail == "Discover"


def test_loan_txn_file_matches_existing_loan_not_card(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Hyundai card · …7788",
        institution="Hyundai",
        current_balance=Decimal("200"),
    )
    loan = Account(
        profile_id=p.id,
        kind="loan",
        nickname="Car loan · Hyundai Palisade",
        institution="Hyundai Motor Finance",
        current_balance=Decimal("28000"),
    )
    s.add_all([card, loan])
    s.commit()
    csv = (
        b"Date,Description,Amount\n"
        b"08/01/2026,REGULAR PAYMENT,-485.00\n"
        b"07/01/2026,REGULAR PAYMENT,-485.00\n"
    )
    plan = build_smart_plan(s, [("hyundai_transaction_details.csv", csv)])
    acc = plan["sources"][0]["accounts"][0]
    assert acc["kind"] == "loan"
    assert acc["action"] == "match"
    assert acc["account_id"] == loan.id
    s.close()


def test_last4_same_kind_wins_over_other_kind(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa · …4411",
        current_balance=Decimal("100"),
    )
    loan = Account(
        profile_id=p.id,
        kind="loan",
        nickname="Student loan · …4411",
        current_balance=Decimal("9000"),
    )
    s.add_all([card, loan])
    s.commit()
    csv = (
        b"Date,Description,Amount\n"
        b"08/01/2026,NELNET PAYMENT,-200.00\n"
    )
    plan = build_smart_plan(s, [("nelnet_student_loan_4411.csv", csv)])
    acc = plan["sources"][0]["accounts"][0]
    assert acc["account_id"] == loan.id
    s.close()


def test_merge_moves_txns_and_undo(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    src = Account(
        profile_id=p.id, kind="loan", nickname="Dup loan", current_balance=Decimal("-485")
    )
    dst = Account(
        profile_id=p.id, kind="loan", nickname="Car loan · Hyundai", current_balance=Decimal("0")
    )
    s.add_all([src, dst])
    s.flush()
    txn = Transaction(
        profile_id=p.id,
        account_id=src.id,
        txn_date=__import__("datetime").date(2026, 8, 1),
        amount=Decimal("-485"),
        payee="REGULAR PAYMENT",
        status="cleared",
    )
    s.add(txn)
    s.commit()
    out = merge_account_into(s, source_id=src.id, target_id=dst.id)
    s.commit()
    s.refresh(txn)
    s.refresh(src)
    s.refresh(dst)
    assert txn.account_id == dst.id
    assert src.archived_at is not None
    assert Decimal(str(dst.current_balance)) == Decimal("-485")
    undo = undo_account_merge(s, out["merge_id"])
    s.commit()
    s.refresh(txn)
    s.refresh(src)
    assert undo["ok"] is True
    assert txn.account_id == src.id
    assert src.archived_at is None
    s.close()
