"""Statements attach to the matching activity file in a mixed batch."""

from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import init_db
from honestspend.seed import seed_all
from honestspend.services.statement_pdf import (
    parse_credit_union_membership,
    parse_vehicle_finance_statement,
)
from honestspend.services import smart_import as smart_import_mod
from honestspend.services.smart_import import (
    _banks_compatible,
    _fingerprints_from_text,
    _txn_fingerprint,
    analyze_upload,
    assign_plan_entities,
    cluster_batch_sources,
    commit_smart_plan,
    extract_owner_names,
    friendly_account_name,
    guess_bank_label,
    guess_card_brand,
    guess_loan_label,
    last4_from_text,
    match_tails_from_acctid,
)


def test_last4_from_amex_and_chase_statement_names():
    assert last4_from_text("Account Ending 6-44112") == "4112"
    assert last4_from_text("Account Ending 2-55223") == "5223"
    assert last4_from_text("Account Number XXXXXX3333") == "3333"
    assert last4_from_text("20260115-statements-4411-.pdf") == "4411"
    assert last4_from_text("Statement_012026_6633.pdf") == "6633"


def test_filename_bank_wins_over_body():
    assert guess_bank_label("Discover-AccountActivity-20260115.pdf", "Issued by Capital One") == "Discover"
    assert guess_bank_label("Chase4411_Activity_20260115.csv") == "Chase"


def test_store_brand_beats_issuing_bank():
    assert guess_card_brand("HOME DEPOT CREDIT SERVICES Issued by Citibank, N.A.") == "Home Depot"
    assert guess_bank_label("stmt.pdf", "HOME DEPOT CREDIT SERVICES Issued by Citibank") == "Home Depot"
    assert guess_card_brand("Target Circle Card Ending in: 4411 TD Bank") == "Target"


def test_friendly_loan_and_store_names():
    assert friendly_account_name(brand="Home Depot", kind="credit", last4="3333") == "Home Depot · …3333"
    assert friendly_account_name(loan_label="Car loan", loan_detail="Hyundai") == "Car loan · Hyundai"
    assert friendly_account_name(loan_label="Home loan") == "Home loan"
    assert guess_loan_label("Rocket Mortgage account details")[0] == "Home loan"
    assert guess_loan_label("HYUNDAI MOTOR FINANCE") == ("Car loan", "Hyundai")


def test_extract_two_business_owners():
    names = extract_owner_names("Prepared for\nNORTH STUDIO LLC\nSOUTH SHOP AGENCY\nAGENCY IN BOX 42")
    joined = " ".join(names).upper()
    assert "SOUTH SHOP AGENCY" in joined or "NORTH STUDIO" in joined
    assert "AGENCY IN BOX 42" in joined or "AGENCY IN BOX" in joined


def test_ofx_match_tails():
    assert "8888" in match_tails_from_acctid("010108888-0002")
    assert "0002" in match_tails_from_acctid("010108888-0002")
    assert "3333" in match_tails_from_acctid("910103333-0091")


def test_cluster_attaches_statement_to_same_last4():
    sources = [
        {
            "file_index": 0,
            "filename": "Chase4411_Activity_20260115.csv",
            "accounts": [
                {
                    "source_key": "csv:Chase4411",
                    "file_format": "csv",
                    "kind": "credit",
                    "last4": "4411",
                    "bank_label": "Chase",
                    "human_title": "Chase · Credit card · …4411",
                    "suggested_nickname": "Chase · credit · …4411",
                    "transactions_found": 40,
                    "action": "create",
                    "what_we_will_do": "Create Chase 4411",
                }
            ],
        },
        {
            "file_index": 1,
            "filename": "20260115-statements-4411-.pdf",
            "accounts": [
                {
                    "source_key": "pdf:4411",
                    "file_format": "pdf",
                    "kind": "credit",
                    "last4": "4411",
                    "bank_label": "Chase",
                    "is_statement": True,
                    "human_title": "Chase · Credit card · …4411",
                    "suggested_nickname": "Chase statement",
                    "transactions_found": 8,
                    "action": "create",
                    "statement_fields": {"min_payment": "35.00"},
                }
            ],
        },
    ]
    cluster_batch_sources(sources)
    assert sources[1]["accounts"][0]["action"] == "attach"
    assert sources[1]["accounts"][0]["attach_to_source_key"] == "csv:Chase4411"
    assert sources[0]["accounts"][0].get("attachments")


def test_cluster_does_not_merge_two_ofx_shares_via_member_number():
    sources = [
        {
            "file_index": 0,
            "filename": "export.ofx",
            "accounts": [
                {
                    "source_key": "ofx:sav",
                    "file_format": "ofx",
                    "kind": "savings",
                    "last4": "0002",
                    "match_tails": ["0002", "8888"],
                    "action": "create",
                    "transactions_found": 10,
                },
                {
                    "source_key": "ofx:chk",
                    "file_format": "ofx",
                    "kind": "checking",
                    "last4": "0001",
                    "match_tails": ["0001", "8888"],
                    "action": "create",
                    "transactions_found": 10,
                },
            ],
        },
        {
            "file_index": 1,
            "filename": "cu-statement.pdf",
            "accounts": [
                {
                    "source_key": "pdf:cu",
                    "file_format": "pdf",
                    "kind": "checking",
                    "last4": "8888",
                    "is_statement": True,
                    "action": "create",
                    "transactions_found": 0,
                }
            ],
        },
    ]
    cluster_batch_sources(sources)
    # Ambiguous member number — leave the statement standalone
    assert sources[1]["accounts"][0]["action"] != "attach"
    assert sources[0]["accounts"][0]["action"] != "attach"
    assert sources[0]["accounts"][1]["action"] != "attach"


def test_assign_named_businesses(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    sources = [
        {
            "file_index": 0,
            "filename": "a.pdf",
            "accounts": [
                {
                    "source_key": "a",
                    "suggested_entity_type": "business",
                    "owner_name": "North Studio LLC",
                    "suggested_nickname": "Blue Business Plus · …4112",
                    "confidence": 0.8,
                    "action": "create",
                }
            ],
        },
        {
            "file_index": 1,
            "filename": "b.pdf",
            "accounts": [
                {
                    "source_key": "b",
                    "suggested_entity_type": "business",
                    "owner_name": "South Shop LLC",
                    "suggested_nickname": "Amazon Business Prime · …6334",
                    "confidence": 0.8,
                    "action": "create",
                }
            ],
        },
        {
            "file_index": 2,
            "filename": "c.csv",
            "accounts": [
                {
                    "source_key": "c",
                    "suggested_entity_type": "personal",
                    "suggested_nickname": "Home Depot · …3333",
                    "confidence": 0.7,
                    "action": "create",
                }
            ],
        },
    ]
    with Session() as session:
        seed_all(session)
        ents = assign_plan_entities(session, sources)
        names = {e["display_name"] for e in ents}
        assert "Personal" in names
        assert any("North Studio" in n for n in names)
        assert any("South Shop" in n for n in names)
        assert sources[0]["accounts"][0]["entity_key"].startswith("business:")
        assert sources[1]["accounts"][0]["entity_key"].startswith("business:")
        assert sources[0]["accounts"][0]["entity_key"] != sources[1]["accounts"][0]["entity_key"]
        assert sources[2]["accounts"][0]["entity_key"] == "personal"
        sources.append(
            {
                "file_index": 3,
                "filename": "mystery.csv",
                "accounts": [
                    {
                        "source_key": "d",
                        "suggested_entity_type": "business",
                        "suggested_nickname": "Office card · …4411",
                        "confidence": 0.6,
                        "action": "create",
                        "review_lines": [],
                    }
                ],
            }
        )
        ents2 = assign_plan_entities(session, sources)
        assert not any(e["key"] == "business" for e in ents2)
        assert sources[3]["accounts"][0].get("needs_owner") is True
        assert sources[3]["accounts"][0]["entity_key"] == "personal"


HYUNDAI_STMT = """
YOUR MONTHLY STATEMENT
Account Number 2616009988
Please make check payable to Hyundai Motor Finance.
HYUNDAI MOTOR FINANCE
Vehicle Description 2024 HYUNDAI TUCSON SEL
VIN Number KMHXX0000TEST0000
Lease Term 36 Months
YOUR TOTAL AMOUNT DUE $400.00
"""

CU_MEMBER = """
Statement July 2026
Account Number XXXXXX8888
Account Summary
SAVINGS ACCOUNT 5.00
FREE CHECKING 0.00
HIGH YIELD MONEY MARKET 100.00
ID: 00 SAVINGS ACCOUNT
Ending Balance for SAVINGS ACCOUNT 5.00
ID: 01 FREE CHECKING
Ending Balance for FREE CHECKING 0.00
07/17 Debit Withdrawal ACH CHASE CREDIT CRD
TYPE: AUTOPAY CO: CHASE CREDIT CRD
ID: 02 HIGH YIELD MONEY MARKET
Ending Balance for HIGH YIELD MONEY MARKET 100.00
canvas.org
Canvas Credit Union
"""


def test_hyundai_motor_finance_is_car_loan(monkeypatch):
    parsed = parse_vehicle_finance_statement(HYUNDAI_STMT)
    assert parsed is not None
    assert parsed["kind"] == "loan"
    assert parsed["last4"] == "9988"
    assert parsed["loan_detail"] and "Tucson" in parsed["loan_detail"]
    monkeypatch.setattr(
        "honestspend.services.statement_pdf.extract_pdf_text",
        lambda *a, **k: (HYUNDAI_STMT, 1),
    )
    monkeypatch.setattr(
        "honestspend.services.statement_pdf.preview_statement_pdf",
        lambda *a, **k: {"candidates": 1, "ending_balance": None},
    )
    accs = analyze_upload("coupon.pdf", b"%PDF-fake")
    assert accs[0]["kind"] == "loan"
    nick = accs[0].get("suggested_nickname") or ""
    assert "Car loan" in nick
    assert "Tucson" in nick or "Hyundai" in nick


def test_cu_membership_splits_shares_not_chase(monkeypatch):
    shares = parse_credit_union_membership(CU_MEMBER)
    assert len(shares) >= 3
    ids = {s["share_id"] for s in shares}
    assert "00" in ids and "01" in ids and "02" in ids
    monkeypatch.setattr(
        "honestspend.services.statement_pdf.extract_pdf_text",
        lambda *a, **k: (CU_MEMBER, 4),
    )
    monkeypatch.setattr(
        "honestspend.services.statement_pdf.preview_statement_pdf",
        lambda *a, **k: {"candidates": 0, "ending_balance": None},
    )
    accs = analyze_upload("Document.pdf", b"%PDF-fake")
    assert len(accs) >= 3
    labels = " ".join(a.get("suggested_nickname") or "" for a in accs)
    assert "Chase" not in labels
    assert any("0001" in (a.get("last4") or "") for a in accs)
    assert any("0002" in (a.get("last4") or "") for a in accs)
    kinds = {a.get("kind") for a in accs}
    assert "checking" in kinds
    assert "savings" in kinds


def test_activity_csv_named_statement_is_primary():
    from honestspend.services.smart_import import _is_statement_acc

    assert _is_statement_acc(
        {"file_format": "csv", "transactions_found": 29, "is_statement": False},
        "Discover-Statement-2026813.csv",
    ) is False
    assert _is_statement_acc(
        {"file_format": "pdf", "transactions_found": 22, "is_statement": True},
        "20260718-statements-9748-.pdf",
    ) is True


def test_loan_csv_attaches_to_loan_statement_not_card():
    sources = [
        {
            "file_index": 0,
            "filename": "Discover-Statement-2026813.csv",
            "accounts": [
                {
                    "source_key": "csv:disc-loan",
                    "file_format": "csv",
                    "kind": "loan",
                    "last4": "3065",
                    "bank_label": "Discover",
                    "human_title": "Personal loan · Discover · …3065",
                    "transactions_found": 29,
                    "action": "create",
                }
            ],
        },
        {
            "file_index": 1,
            "filename": "Discover-Statement-20260728-3065.pdf",
            "accounts": [
                {
                    "source_key": "pdf:disc-loan",
                    "file_format": "pdf",
                    "kind": "loan",
                    "last4": "3065",
                    "bank_label": "Discover",
                    "is_statement": True,
                    "human_title": "Personal loan · Discover · …3065",
                    "transactions_found": 0,
                    "action": "create",
                }
            ],
        },
        {
            "file_index": 2,
            "filename": "Discover-card-3065.csv",
            "accounts": [
                {
                    "source_key": "csv:disc-card",
                    "file_format": "csv",
                    "kind": "credit",
                    "last4": "3065",
                    "bank_label": "Discover",
                    "human_title": "Discover · …3065",
                    "transactions_found": 40,
                    "action": "create",
                }
            ],
        },
    ]
    cluster_batch_sources(sources)
    assert sources[1]["accounts"][0]["action"] == "attach"
    assert sources[1]["accounts"][0]["attach_to_source_key"] == "csv:disc-loan"
    assert sources[2]["accounts"][0]["action"] != "attach"


def test_card_no_column_and_loan_header_from_real_layouts():
    cap = (
        "Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        "2026-08-12,2026-08-12,8857,CAPITAL ONE AUTOPAY PYMT,Payment/Credit,,169.90\n"
        "2026-07-21,2026-07-22,8857,LOVELAND PULSE,Phone/Cable,99.95,\n"
    ).encode()
    accs = analyze_upload("2026-08-13_transaction_download.csv", cap)
    assert accs[0]["kind"] == "credit"
    assert accs[0]["last4"] == "8857"
    assert accs[0]["bank_label"] == "Capital One"

    loan = (
        '"Discover, a division of Capital One, N.A., (Discover) Account Activity",,\n'
        ",,\n"
        "Account Ending in 3065,,\n"
        "Discover Loan Account,,\n"
        ",,\n"
        "Date,Description,Amount\n"
        "07/17/2026,Web Payment,-370.92\n"
        "06/17/2026,Web Payment,-370.92\n"
        "05/17/2026,Web Payment,-370.92\n"
        "04/17/2026,Web Payment,-370.92\n"
        "03/17/2026,Web Payment,-370.92\n"
        "02/17/2026,Web Payment,-370.92\n"
        "01/17/2026,Web Payment,-370.92\n"
        "12/17/2025,Web Payment,-370.92\n"
    ).encode()
    accs = analyze_upload("Discover-Statement-2026813.csv", loan)
    assert accs[0]["kind"] == "loan"
    assert accs[0]["last4"] == "3065"
    assert accs[0]["loan_label"] == "Personal loan"


def test_cu_register_payees_do_not_become_apple_card_or_mortgage():
    csv = (
        "Posting Date,Description,Amount\n"
        "8/6/2026,GREENBRIAR PAIRE TYPE: Assoc Pmt,-130.00\n"
        "8/5/2026,From Share 01,75.00\n"
        "8/5/2026,Payment to Apple Card,-80.00\n"
        "8/3/2026,Payment to Rocket Mortgage TYPE: LOAN,-1595.03\n"
        "8/2/2026,From Share 01,200.00\n"
        "8/1/2026,XCEL ENERGY,-90.00\n"
        "7/30/2026,From Share 01,50.00\n"
        "7/28/2026,SAFEWAY,-40.00\n"
    ).encode()
    accs = analyze_upload("ExportedTransactions.csv", csv)
    title = (accs[0].get("human_title") or "") + " " + (accs[0].get("suggested_nickname") or "")
    assert accs[0]["kind"] != "loan"
    assert "Apple Card" not in title
    assert "Home loan" not in title
    assert accs[0]["kind"] in ("checking", "savings")


def test_same_last4_different_issuer_does_not_attach():
    """Amex …1009 must not swallow a Scheels/FNBO …1009 statement."""
    sources = [
        {
            "file_index": 0,
            "filename": "activity (1).xlsx",
            "accounts": [
                {
                    "source_key": "xlsx:amex",
                    "file_format": "xlsx",
                    "kind": "credit",
                    "last4": "1009",
                    "brand": "Blue Business Plus",
                    "bank_label": "Blue Business Plus",
                    "human_title": "Blue Business Plus · …1009",
                    "transactions_found": 39,
                    "action": "create",
                }
            ],
        },
        {
            "file_index": 1,
            "filename": "Transaction Site - Account Details.pdf",
            "accounts": [
                {
                    "source_key": "pdf:scheels-txns",
                    "file_format": "pdf",
                    "kind": "credit",
                    "last4": "1009",
                    "brand": "Scheels",
                    "bank_label": "Scheels",
                    "human_title": "Scheels · …1009",
                    "transactions_found": 6,
                    "action": "create",
                }
            ],
        },
        {
            "file_index": 2,
            "filename": "2025-10-17.pdf",
            "accounts": [
                {
                    "source_key": "pdf:scheels-stmt",
                    "file_format": "pdf",
                    "kind": "credit",
                    "last4": "1009",
                    "brand": "Scheels",
                    "bank_label": "FNBO",
                    "is_statement": True,
                    "human_title": "Scheels · …1009",
                    "statement_fields": {"credit_limit": "13700.00"},
                    "transactions_found": 0,
                    "action": "create",
                }
            ],
        },
    ]
    cluster_batch_sources(sources)
    assert sources[2]["accounts"][0]["action"] == "attach"
    assert sources[2]["accounts"][0]["attach_to_source_key"] == "pdf:scheels-txns"
    assert sources[0]["accounts"][0]["action"] != "attach"
    assert not any(
        (att.get("filename") == "2025-10-17.pdf")
        for att in (sources[0]["accounts"][0].get("attachments") or [])
    )


def test_discover_and_capital_one_are_not_the_same_account():
    assert guess_bank_label("Discover-AccountActivity-20260115.pdf", "Discover, a division of Capital One") == "Discover"
    assert guess_bank_label("activity.csv", "© 2026 Capital One, N.A.  Discover is a division of Capital One") == "Discover"
    # Same parent company, different products — do not glue a Discover card to a Capital One card
    assert not _banks_compatible({"bank_label": "Discover"}, {"bank_label": "Capital One"})
    assert not _banks_compatible({"org": "Capital One"}, {"org": "Discover"})


def test_fingerprints_from_discover_activity_layout():
    text = (
        "07/15/26 07/15/26 INTERNET PAYMENT - THANK YOU\n"
        "DIRECTPAY\n"
        "FULL BALANCE\n"
        "$ -80.00 Payment\n"
        "06/22/26 06/22/26 AUTOMATIC STATEMENT CREDIT $ -22.11 Award\n"
    )
    cores = {fp.rsplit("|", 1)[0] for fp in _fingerprints_from_text(text)}
    assert cores == {"2026-07-15|8000", "2026-06-22|2211"}


def test_cluster_discover_pdf_to_csv_by_transactions():
    csv_fps = [
        _txn_fingerprint("2026-06-22", "-22.11", "AUTOMATIC STATEMENT CREDIT"),
        _txn_fingerprint("2026-07-15", "-80.00", "DIRECTPAY FULL BALANCE"),
    ]
    # PDF payee is truncated vs the CSV column — match on date + cents
    pdf_fps = [
        _txn_fingerprint("06/22/26", "$ -22.11", "AUTOMATIC STATEMENT CREDIT"),
        _txn_fingerprint("07/15/26", "$ -80.00", "DIRECTPAY"),
    ]
    sources = [
        {
            "file_index": 0,
            "filename": "Discover-AllAvailable-20260115.csv",
            "accounts": [
                {
                    "source_key": "csv:disc",
                    "file_format": "csv",
                    "kind": "credit",
                    "bank_label": "Discover",
                    "txn_fps": csv_fps,
                    "action": "create",
                    "transactions_found": 20,
                }
            ],
        },
        {
            "file_index": 1,
            "filename": "Discover-AccountActivity-20260115.pdf",
            "accounts": [
                {
                    "source_key": "pdf:disc",
                    "file_format": "pdf",
                    "kind": "credit",
                    "bank_label": "Capital One",
                    "last4": "4411",
                    "is_statement": True,
                    "txn_fps": pdf_fps,
                    "action": "create",
                    "transactions_found": 2,
                }
            ],
        },
    ]
    cluster_batch_sources(sources)
    assert sources[1]["accounts"][0]["action"] == "attach"
    assert sources[1]["accounts"][0]["attach_to_source_key"] == "csv:disc"
    assert sources[0]["accounts"][0].get("last4") == "4411"


def test_qif_analyze_includes_txn_fingerprints():
    qif = (
        "!Type:CCard\n"
        "D07/15/26\n"
        "T-80.00\n"
        "PDIRECTPAY FULL BALANCE\n"
        "^\n"
    )
    accs = analyze_upload("download.qif", qif.encode("utf-8"))
    assert accs
    fps = accs[0].get("txn_fps") or []
    assert fps
    assert any("8000" in str(fp) for fp in fps)


def test_cluster_statement_pdf_to_qif_by_transactions():
    qif_fps = [
        _txn_fingerprint("2026-07-15", "-80.00", "DIRECTPAY FULL BALANCE"),
        _txn_fingerprint("2026-06-22", "-22.11", "AUTOMATIC STATEMENT CREDIT"),
    ]
    pdf_fps = [
        _txn_fingerprint("07/15/26", "-80.00", "DIRECTPAY"),
        _txn_fingerprint("06/22/26", "-22.11", "AUTOMATIC STATEMENT CREDIT"),
    ]
    sources = [
        {
            "file_index": 0,
            "filename": "transaction_download.qif",
            "accounts": [
                {
                    "source_key": "qif:ccard",
                    "file_format": "qif",
                    "kind": "credit",
                    "bank_label": "Capital One",
                    "txn_fps": qif_fps,
                    "action": "create",
                    "transactions_found": 23,
                }
            ],
        },
        {
            "file_index": 1,
            "filename": "Statement_072026_4411.pdf",
            "accounts": [
                {
                    "source_key": "pdf:stmt",
                    "file_format": "pdf",
                    "kind": "credit",
                    "bank_label": "Capital One",
                    "last4": "4411",
                    "is_statement": True,
                    "txn_fps": pdf_fps,
                    "action": "create",
                    "transactions_found": 2,
                }
            ],
        },
    ]
    cluster_batch_sources(sources)
    assert sources[1]["accounts"][0]["action"] == "attach"
    assert sources[1]["accounts"][0]["attach_to_source_key"] == "qif:ccard"
    assert sources[0]["accounts"][0].get("last4") == "4411"


def test_fingerprints_mmdd_uses_statement_year():
    text = (
        "WELLS FARGO REFLECT\nAccount ending in 9690\n"
        "Statement Period 07/09/2026 to 08/07/2026\n"
        "Payment Due Date 09/02/2026\nMinimum Payment $83.00\n"
        "Transactions\n"
        "07/23 07/23 7414718JX0 ONLINE ACH PAYMENT THANK YOU 1,320.00\n"
        "07/02 07/02 AUTOMATIC PAYMENT - THANK YOU 97.00\n"
    )
    fps = _fingerprints_from_text(text)
    cores = {str(x).rsplit("|", 1)[0] for x in fps}
    assert "2026-07-23|132000" in cores
    assert "2026-07-02|9700" in cores
    assert not any(c.startswith("2026-09-02") for c in cores)


def test_first_four_line_items_date_amount_picks_the_right_file():
    """First 3–4 posted date→amount pairs beat last-4 / bank labels."""
    stmt_fps = [
        _txn_fingerprint("2026-08-12", "169.90", "AUTOPAY"),
        _txn_fingerprint("2026-07-24", "30.00", "CASH BACK"),
        _txn_fingerprint("2026-07-21", "99.95", "LOVELAND PULSE"),
        _txn_fingerprint("2026-06-22", "99.95", "LOVELAND PULSE"),
    ]
    hit_fps = list(stmt_fps) + [_txn_fingerprint("2026-05-16", "199.90", "AUTOPAY")]
    miss_fps = [
        _txn_fingerprint("2026-08-11", "21.52", "XCEL"),
        _txn_fingerprint("2026-02-20", "12.99", "PARAMOUNT"),
        _txn_fingerprint("2026-01-20", "12.99", "PAYMENT"),
        _txn_fingerprint("2025-12-29", "12.99", "PARAMOUNT"),
    ]
    sources = [
        {
            "file_index": 0,
            "filename": "other.csv",
            "accounts": [
                {
                    "source_key": "csv:other",
                    "file_format": "csv",
                    "kind": "credit",
                    "last4": "7687",
                    "bank_label": "Target",
                    "txn_fps": miss_fps,
                    "transactions_found": 20,
                    "action": "create",
                }
            ],
        },
        {
            "file_index": 1,
            "filename": "2026-08-13_transaction_download.csv",
            "accounts": [
                {
                    "source_key": "csv:cap1",
                    "file_format": "csv",
                    "kind": "credit",
                    "last4": None,
                    "bank_label": "Capital One",
                    "txn_fps": hit_fps,
                    "transactions_found": 24,
                    "action": "create",
                }
            ],
        },
        {
            "file_index": 2,
            "filename": "Statement_072026_8857.pdf",
            "accounts": [
                {
                    "source_key": "pdf:cap1",
                    "file_format": "pdf",
                    "kind": "credit",
                    "last4": "8857",
                    "is_statement": True,
                    "txn_fps": stmt_fps,
                    "transactions_found": 4,
                    "action": "create",
                }
            ],
        },
    ]
    cluster_batch_sources(sources)
    assert sources[2]["accounts"][0]["action"] == "attach"
    assert sources[2]["accounts"][0]["attach_to_source_key"] == "csv:cap1"
    assert sources[1]["accounts"][0].get("last4") == "8857"


def test_commit_applies_statement_to_qif_account(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    captured: dict = {}

    class _Res:
        transactions_created = 0
        skipped_existing = 0
        errors: list = []
        ending_balance = None
        categorized = 0
        next_steps: list = []

    def fake_pdf_import(session, *, account_id, file_obj=None, filename=None, **k):
        captured["account_id"] = account_id
        return _Res()

    monkeypatch.setattr(
        "honestspend.services.statement_pdf.import_statement_pdf",
        fake_pdf_import,
    )

    qif = (
        "!Type:CCard\n"
        "D07/15/26\n"
        "T-80.00\n"
        "PDIRECTPAY FULL BALANCE\n"
        "^\n"
    ).encode("utf-8")
    pdf = b"%PDF-1.4 fake-stmt"

    real_analyze = smart_import_mod.analyze_upload

    def fake_analyze(filename, content):
        if str(filename).lower().endswith(".pdf"):
            return [
                {
                    "source_key": f"pdf:{filename}",
                    "file_format": "pdf",
                    "kind": "credit",
                    "is_statement": True,
                    "suggested_nickname": "Capital One statement",
                    "last4": "4411",
                }
            ]
        return real_analyze(filename, content)

    monkeypatch.setattr(smart_import_mod, "analyze_upload", fake_analyze)

    qif_accs = analyze_upload("card.qif", qif)
    assert qif_accs
    qsk = qif_accs[0]["source_key"]
    plan = {
        "entities": [{"key": "personal", "entity_type": "personal", "display_name": "Personal", "action": "create"}],
        "sources": [
            {
                "file_index": 0,
                "filename": "card.qif",
                "accounts": [{**qif_accs[0], "action": "create", "entity_key": "personal"}],
            },
            {
                "file_index": 1,
                "filename": "Statement.pdf",
                "accounts": [
                    {
                        "source_key": "pdf:Statement.pdf",
                        "file_format": "pdf",
                        "kind": "credit",
                        "action": "attach",
                        "attach_to_file_index": 0,
                        "attach_to_source_key": qsk,
                        "entity_key": "personal",
                        "is_statement": True,
                    }
                ],
            },
        ],
    }
    with Session() as session:
        seed_all(session)
        out = commit_smart_plan(
            session,
            plan=plan,
            files=[("card.qif", qif), ("Statement.pdf", pdf)],
            auto_categorize=False,
        )
        session.commit()
        from honestspend.db import Account

        cards = [a for a in session.query(Account).all() if a.kind == "credit"]
        pdf_res = next((r for r in out.get("results") or [] if r.get("format") == "pdf"), {})
        qif_res = next((r for r in out.get("results") or [] if r.get("format") == "qif"), {})
        assert pdf_res.get("error") in (None, "", [])
        qif_id = qif_res.get("account_id") or (qif_res.get("accounts") or [{}])[0].get("account_id")
        assert captured.get("account_id")
        if qif_id:
            assert captured["account_id"] == qif_id
        assert captured["account_id"] in {a.id for a in cards}
