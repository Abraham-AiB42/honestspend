"""QIF import."""

from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.bank_qif import (
    import_qif,
    import_qif_multi,
    parse_qif_accounts,
    parse_qif_transactions,
    preview_qif,
)
from honestspend.db import Profile

SAMPLE_QIF = """!Type:Bank
D08/01/2026
T-45.67
PCOFFEE SHOP
MLATTE
^
D08/05/2026
T1500.00
PPAYROLL ACME
^
"""

MULTI_QIF = """!Account
NPrimary checking
TBank
^
!Type:Bank
D08/01/2026
T-12.00
PGROCERY
^
!Account
NEveryday card
TCcard
^
!Type:CCard
D08/02/2026
T-89.50
PAmazon
^
"""


def _session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    return sessionmaker(bind=engine)()


def test_parse_qif_sample():
    rows, meta = parse_qif_transactions(SAMPLE_QIF)
    assert len(rows) == 2
    assert rows[0]["amount"] == Decimal("-45.67")
    assert rows[0]["txn_date"].isoformat() == "2026-08-01"
    assert "COFFEE" in rows[0]["payee"].upper()
    assert rows[1]["amount"] == Decimal("1500.00")
    assert meta.get("account_count") == 1


def test_parse_multi_account_qif():
    accts = parse_qif_accounts(MULTI_QIF)
    assert len(accts) == 2
    assert accts[0]["name"] == "Primary checking"
    assert accts[0]["kind"] == "checking"
    assert accts[0]["transactions_found"] == 1
    assert accts[1]["name"] == "Everyday card"
    assert accts[1]["kind"] == "credit"
    assert accts[1]["rows"][0]["amount"] == Decimal("-89.50")


def test_preview_qif():
    prev = preview_qif(SAMPLE_QIF.encode("utf-8"))
    assert prev["ok"] is True
    assert prev["transactions_found"] == 2


def test_import_qif_creates_txns(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    try:
        seed_all(session)
        prof = session.query(Profile).first()
        acct = Account(
            profile_id=prof.id,
            kind="checking",
            nickname="Primary checking",
            current_balance=Decimal("0"),
            is_cash_for_ifpp=True,
        )
        session.add(acct)
        session.flush()
        res = import_qif(
            session,
            account_id=acct.id,
            file_obj=SAMPLE_QIF.encode("utf-8"),
            filename="test.qif",
            auto_categorize=False,
        )
        session.commit()
        assert res.transactions_created == 2
        assert session.query(Transaction).filter(Transaction.account_id == acct.id).count() >= 2
        res2 = import_qif(
            session,
            account_id=acct.id,
            file_obj=SAMPLE_QIF.encode("utf-8"),
            filename="test.qif",
            auto_categorize=False,
        )
        assert res2.skipped_existing == 2
        assert res2.transactions_created == 0
    finally:
        session.close()


def test_import_qif_multi(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    try:
        seed_all(session)
        out = import_qif_multi(
            session,
            file_obj=MULTI_QIF.encode("utf-8"),
            filename="multi.qif",
            auto_categorize=False,
            auto_create_accounts=True,
        )
        session.commit()
        assert out["ok"] is True
        assert out["transactions_created"] == 2
        assert out["account_count"] == 2
    finally:
        session.close()
