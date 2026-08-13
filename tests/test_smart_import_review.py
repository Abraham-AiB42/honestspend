"""Smart import review labels (bank / last4) for average users."""

from honestspend.services.smart_import import (
    _sample_postings,
    enrich_review_fields,
    guess_bank_label,
    last4_from_text,
    build_smart_plan,
)
from honestspend.config import settings
from honestspend.db import init_db
from honestspend.seed import seed_all
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

OFX = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1>
<SONRS>
<FI>
<ORG>Canvas Credit Union
<FID>1234
</FI>
</SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<BANKACCTFROM>
<BANKID>307070005
<ACCTID>000012345678
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260801
<TRNAMT>-12.34
<FITID>1
<NAME>COFFEE
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>500.00
<DTASOF>20260801
</LEDGERBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


def test_sample_postings_include_amount():
    rows = [
        {"date": "2026-06-22", "payee": "AUTOMATIC STATEMENT CREDIT", "amount": "-22.11"},
        {"txn_date": "2026-07-15", "payee": "DIRECTPAY FULL BALANCE", "amount": "-80.00"},
        {"payee": "COFFEE", "amount": "4.50"},
    ]
    posts = _sample_postings(rows)
    assert len(posts) == 3
    assert posts[0]["amount"] == "-$22.11"
    assert "AUTOMATIC STATEMENT CREDIT" in posts[0]["line"]
    assert "-$22.11" in posts[0]["line"]
    assert posts[1]["amount"] == "-$80.00"
    assert posts[2]["amount"] == "$4.50"


def test_guess_bank_and_last4():
    assert guess_bank_label("chase_sapphire_export.csv") == "Chase Sapphire"
    assert guess_bank_label("Chase4411_Activity_20260115.csv") == "Chase"
    assert last4_from_text("Primary-checking-4521.ofx") == "4521"
    assert last4_from_text("card ****8899 statement") == "8899"
    assert last4_from_text("Chase4411_Activity_20260115.csv") == "4411"


def test_enrich_review_fields():
    acc = {
        "kind": "checking",
        "org": "Canvas Credit Union",
        "acctid": "000012345678",
        "file_format": "ofx",
        "transactions_found": 10,
        "ledger_balance": "500.00",
        "suggested_entity_type": "personal",
        "confidence": 0.8,
        "sample_payees": ["COFFEE", "PAYROLL"],
        "sample_postings": [
            {"date": "2026-08-01", "payee": "COFFEE", "amount": "-$4.50", "line": "2026-08-01  COFFEE  -$4.50"},
            {"date": "2026-08-02", "payee": "PAYROLL", "amount": "$2,000.00", "line": "2026-08-02  PAYROLL  $2,000.00"},
        ],
        "reasons": [],
    }
    out = enrich_review_fields(acc, filename="canvas_all.ofx")
    assert out["last4"] == "5678"
    assert "Canvas" in (out.get("bank_label") or out.get("human_title") or "")
    assert out["review_lines"]
    assert any("5678" in x or "last" in x.lower() or "ending" in x.lower() for x in out["review_lines"])
    assert "5678" in out["suggested_nickname"] or "…5678" in out["suggested_nickname"]
    sample_line = " ".join(out["review_lines"])
    assert "COFFEE" in sample_line and "$4.50" in sample_line


def test_build_plan_includes_review(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        seed_all(session)
        plan = build_smart_plan(session, [("canvas_export.ofx", OFX.encode("utf-8"))])
        assert plan["ok"] is True
        acc = plan["sources"][0]["accounts"][0]
        assert acc.get("review_lines")
        assert acc.get("human_title")
        assert acc.get("last4") == "5678"
        assert "Canvas" in (acc.get("bank_label") or "") or "Canvas" in (acc.get("human_title") or "")
        posts = acc.get("sample_postings") or []
        assert posts
        assert any("12.34" in (p.get("amount") or p.get("line") or "") for p in posts)
        assert any("COFFEE" in (p.get("payee") or p.get("line") or "") for p in posts)
