"""OFX/QFX import."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.bank_ofx import import_ofx, parse_ofx_transactions, preview_ofx
from financial_os.services.import_inbox import ensure_inbox_layout, process_inbox
from financial_os.services.onboarding import apply_first_run

SAMPLE_OFX = """OFXHEADER:100
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
<ACCTID>123456789
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260801
<TRNAMT>-45.67
<FITID>20260801001
<NAME>COFFEE SHOP
<MEMO>LATTE
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260805
<TRNAMT>1500.00
<FITID>20260805002
<NAME>PAYROLL ACME
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>2554.33
<DTASOF>20260805
</LEDGERBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


def test_parse_ofx_sample():
    rows, meta = parse_ofx_transactions(SAMPLE_OFX)
    assert len(rows) == 2
    assert meta.get("acctid") == "123456789"
    assert meta.get("ledger_balance") == "2554.33"
    assert meta.get("ledger_as_of") == "2026-08-05"
    assert rows[0]["amount"] == Decimal("-45.67")
    assert rows[0]["txn_date"].isoformat() == "2026-08-01"
    assert "COFFEE" in rows[0]["payee"].upper()
    assert rows[1]["amount"] == Decimal("1500.00")


def test_preview_ofx():
    prev = preview_ofx(SAMPLE_OFX.encode("utf-8"))
    assert prev["ok"] is True
    assert prev["transactions_found"] == 2
    assert prev["ledger_balance"] == "2554.33"


SAMPLE_CREDIT_OFX = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<CREDITCARDMSGSRSV1>
<CCSTMTTRNRS>
<CCSTMTRS>
<CCACCTFROM>
<ACCTID>4111111111111111
</CCACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260801
<TRNAMT>45.99
<FITID>cc20260801001
<NAME>AMAZON
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260805
<TRNAMT>-100.00
<FITID>cc20260805002
<NAME>PAYMENT THANK YOU
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>145.99
<DTASOF>20260805
</LEDGERBAL>
</CCSTMTRS>
</CCSTMTTRNRS>
</CREDITCARDMSGSRSV1>
</OFX>
"""


def test_credit_ofx_normalizer_payment(tmp_path: Path, monkeypatch):
    """Credit OFX: positive charge + negative payment → correct ledger signs and balance."""
    from financial_os.db import Profile

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 'financial_os.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="credit",
        nickname="Visa OFX",
        current_balance=Decimal("200"),
        credit_limit=Decimal("1000"),
        is_cash_for_ifpp=False,
    )
    s.add(acct)
    s.flush()
    r = import_ofx(
        s,
        account_id=acct.id,
        file_obj=SAMPLE_CREDIT_OFX.encode("utf-8"),
        filename="card.ofx",
        auto_categorize=False,
    )
    assert r.transactions_created == 2
    by_payee = {t.payee.upper(): float(t.amount) for t in s.query(Transaction).all()}
    assert any("AMAZON" in k and by_payee[k] == -45.99 for k in by_payee)
    assert any("PAYMENT" in k and by_payee[k] == 100.0 for k in by_payee)
    s.refresh(acct)
    # 200 + 45.99 - 100 = 145.99
    assert acct.current_balance == Decimal("145.99")
    s.close()


def test_import_ofx_and_dedupe(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 'financial_os.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("100"))
    s.commit()
    acct = s.query(Account).filter(Account.kind == "checking").one()

    r1 = import_ofx(s, account_id=acct.id, file_obj=SAMPLE_OFX.encode("utf-8"), filename="a.ofx")
    s.commit()
    assert r1.transactions_created == 2
    assert s.query(Transaction).count() == 2
    assert r1.institution_balance_set is True
    assert r1.ledger_balance == "2554.33"
    assert r1.drift is not None
    assert r1.next_steps
    s.refresh(acct)
    assert acct.institution_balance == Decimal("2554.33")

    r2 = import_ofx(s, account_id=acct.id, file_obj=SAMPLE_OFX.encode("utf-8"), filename="a.ofx")
    s.commit()
    assert r2.transactions_created == 0
    assert r2.skipped_existing == 2
    s.close()


def test_ofx_last4_unique_only(tmp_path: Path, monkeypatch):
    from financial_os.services.import_inbox import resolve_account_for_file
    from financial_os.services.bank_ofx import ofx_external_account_key

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 'financial_os.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("100"))
    s.flush()
    acct = s.query(Account).filter(Account.kind == "checking").one()
    # Two accounts with same last-4 in nickname — last4 must not match
    acct.nickname = "Checking 6789"
    from financial_os.db import Profile

    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    s.add(
        Account(
            profile_id=personal.id,
            kind="savings",
            nickname="Save 6789",
            current_balance=Decimal("50"),
            is_cash_for_ifpp=True,
        )
    )
    s.flush()
    ofx_path = tmp_path / "x.ofx"
    # ACCTID ends in 6789
    ofx_path.write_text(
        SAMPLE_OFX.replace("123456789", "111116789"),
        encoding="utf-8",
    )
    matched, score, mode = resolve_account_for_file(s, ofx_path)
    assert mode != "ofx_last4"  # ambiguous last-4
    # Unique external_id still wins
    acct.external_id = ofx_external_account_key("111116789")
    s.flush()
    matched2, _, mode2 = resolve_account_for_file(s, ofx_path)
    assert mode2 == "ofx_acctid"
    assert matched2 is not None and matched2.id == acct.id
    s.close()


def test_inbox_ofx(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 'financial_os.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("100"))
    s.commit()
    layout = ensure_inbox_layout()
    path = Path(layout["inbox"]) / "Primary-checking.ofx"
    path.write_text(SAMPLE_OFX, encoding="utf-8")
    result = process_inbox(s)
    s.commit()
    assert result["transactions_created"] >= 1
    assert result.get("next_steps")
    assert not path.exists()  # archived
    # ACCTID learned on account
    acct = s.query(Account).filter(Account.kind == "checking").one()
    assert acct.external_id == "ofx:123456789"

    # Second drop: match by learned ACCTID even with unrelated filename
    path2 = Path(layout["inbox"]) / "random-export.ofx"
    path2.write_text(SAMPLE_OFX, encoding="utf-8")
    result2 = process_inbox(s)
    s.commit()
    assert result2["files_seen"] == 1
    assert result2.get("ofx_acctid_matches", 0) >= 1
    row = result2["results"][0]
    assert row.get("match_mode") == "ofx_acctid"
    assert row.get("account_id") == acct.id
    s.close()
