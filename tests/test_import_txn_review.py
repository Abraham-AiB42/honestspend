"""D12: statement never overwrites books txns; amount/date mismatch → review."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.bank_ofx import import_ofx
from honestspend.services.import_txn_review import txn_mismatch_reviews


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


def test_txn_mismatch_reviews_amount_diff():
    """Pure helper: same identity, amount differs → review_txn_mismatch."""
    existing = Transaction(
        id=42,
        profile_id=1,
        account_id=1,
        txn_date=date(2026, 8, 1),
        amount=Decimal("-40.00"),
        payee="COFFEE",
        external_id="ofx:1:FIT40",
        status="cleared",
    )
    # SQLAlchemy may not assign id without session — set explicitly for unit test
    existing.id = 42
    rev = txn_mismatch_reviews(
        existing=existing,
        incoming={
            "amount": Decimal("-45.00"),
            "txn_date": date(2026, 8, 1),
            "payee": "COFFEE",
        },
    )
    assert rev is not None
    assert rev["action"] == "review_txn_mismatch"
    assert rev["txn_id"] == 42
    assert rev["books_amount"] == "-40.00" or Decimal(str(rev["books_amount"])) == Decimal(
        "-40.00"
    )
    assert rev["statement_amount"] == "-45.00" or Decimal(
        str(rev["statement_amount"])
    ) == Decimal("-45.00")


def test_txn_mismatch_reviews_same_amount_no_review():
    existing = Transaction(
        profile_id=1,
        account_id=1,
        txn_date=date(2026, 8, 1),
        amount=Decimal("-40.00"),
        payee="COFFEE",
        external_id="ofx:1:FIT40",
        status="cleared",
    )
    existing.id = 7
    assert (
        txn_mismatch_reviews(
            existing=existing,
            incoming={
                "amount": Decimal("-40.00"),
                "txn_date": date(2026, 8, 1),
            },
        )
        is None
    )


def test_ofx_same_fitid_amount_mismatch_keeps_books_and_reviews(
    tmp_path: Path, monkeypatch
):
    """Books −40 on 2026-08-01; statement same FITID amount −45 → books stay −40."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("1000.00"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    books = Transaction(
        profile_id=p.id,
        account_id=acct.id,
        txn_date=date(2026, 8, 1),
        amount=Decimal("-40.00"),
        payee="COFFEE SHOP",
        status="cleared",
        external_id=f"ofx:{acct.id}:FIT40",
        memo="books",
    )
    s.add(books)
    s.commit()
    books_id = books.id

    ofx = f"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<BANKACCTFROM>
<BANKID>121000248
<ACCTID>999888777
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260801
<TRNAMT>-45.00
<FITID>FIT40
<NAME>COFFEE SHOP
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>955.00
<DTASOF>20260801
</LEDGERBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""
    result = import_ofx(
        s,
        account_id=acct.id,
        file_obj=ofx.encode("utf-8"),
        filename="mismatch.ofx",
        auto_categorize=False,
    )
    s.commit()

    row = s.get(Transaction, books_id)
    assert row is not None
    assert row.amount == Decimal("-40.00")
    assert row.txn_date == date(2026, 8, 1)
    assert row.payee == "COFFEE SHOP"

    # No second row created for the mismatched FITID
    same_fit = (
        s.query(Transaction)
        .filter(Transaction.account_id == acct.id, Transaction.external_id == f"ofx:{acct.id}:FIT40")
        .count()
    )
    assert same_fit == 1

    steps = result.next_steps or []
    assert any(st.get("action") == "review_txn_mismatch" for st in steps)
    mismatches = getattr(result, "txn_mismatches", None) or []
    if mismatches:
        assert any(
            m.get("action") == "review_txn_mismatch"
            or m.get("txn_id") == books_id
            for m in mismatches
        )

    s.close()


def test_ofx_omitted_books_txn_still_present(tmp_path: Path, monkeypatch):
    """Statement omits a books txn → books row still present (never delete)."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("500.00"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    orphan = Transaction(
        profile_id=p.id,
        account_id=acct.id,
        txn_date=date(2026, 7, 15),
        amount=Decimal("-12.00"),
        payee="ONLY IN BOOKS",
        status="cleared",
        external_id=f"ofx:{acct.id}:ORPHAN1",
    )
    s.add(orphan)
    s.commit()
    orphan_id = orphan.id

    ofx = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<BANKACCTFROM>
<BANKID>121000248
<ACCTID>111
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260810
<TRNAMT>-5.00
<FITID>NEWONLY
<NAME>NEW CHARGE
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""
    import_ofx(
        s,
        account_id=acct.id,
        file_obj=ofx.encode("utf-8"),
        filename="partial.ofx",
        auto_categorize=False,
    )
    s.commit()

    still = s.get(Transaction, orphan_id)
    assert still is not None
    assert still.payee == "ONLY IN BOOKS"
    assert still.amount == Decimal("-12.00")
    # New statement row still imported
    n = s.query(Transaction).filter(Transaction.account_id == acct.id).count()
    assert n == 2

    s.close()
