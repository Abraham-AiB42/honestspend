"""Amex-style activity.xlsx parse + smart-import classification (synthetic fixtures)."""

from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import init_db
from honestspend.seed import seed_all
from honestspend.services.activity_xlsx import parse_activity_xlsx
from honestspend.services.smart_import import (
    analyze_upload,
    build_smart_plan,
    guess_account_kind,
    last4_from_text,
)


def _amex_book(*, product: str, mask: str, payees: list[tuple[str, float, str]]) -> bytes:
    wb = Workbook()
    details = wb.active
    details.title = "Transaction Details"
    details.append(["Transaction Details", f"{product} / Jul 23, 2026 to Aug 21, 2026"])
    details.append(["Prepared for", ""])
    details.append(["JANE SAMPLE", ""])
    details.append(["Account Number", ""])
    details.append([mask, ""])
    details.append(["", ""])
    details.append(
        ["Date", "Description", "Amount", "Extended Details", "Reference", "Category"]
    )
    for payee, amt, cat in payees:
        details.append(["08/09/2026", payee, amt, "", "32026REF", cat])
    summary = wb.create_sheet("Transaction Summary")
    summary.append(["Transaction Summary", f"{product} / Jul 23, 2026 to Aug 21, 2026"])
    summary.append(["Prepared for", ""])
    summary.append(["JANE SAMPLE", ""])
    summary.append(["Account Number", ""])
    summary.append([mask, ""])
    summary.append(["", ""])
    summary.append(["Total Balance", 100.00])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_amex_business_card():
    raw = _amex_book(
        product="Blue Business Plus Card",
        mask="XXXX-XXXXXX-44112",
        payees=[("OFFICE HOSTING CO", 11.86, "Other-Miscellaneous")],
    )
    parsed = parse_activity_xlsx(raw, "activity (1).xlsx")
    assert parsed is not None
    assert parsed["kind"] == "credit"
    assert parsed["org"] == "American Express"
    assert parsed["last4"] == "4112"
    assert parsed["suggested_entity_type"] == "business"
    assert parsed["product"] and "Business" in parsed["product"]
    assert parsed["transactions_found"] == 1


def test_parse_amex_personal_hilton():
    raw = _amex_book(
        product="Hilton Honors Surpass® Card",
        mask="XXXX-XXXXXX-55223",
        payees=[("SAMPLE CAFE", 55.0, "Restaurant-Bar & Café")],
    )
    parsed = parse_activity_xlsx(raw, "activity.xlsx")
    assert parsed is not None
    assert parsed["suggested_entity_type"] == "personal"
    assert parsed["last4"] == "5223"


def test_analyze_upload_xlsx_names_card():
    raw = _amex_book(
        product="Blue Business Cash(TM)",
        mask="XXXX-XXXXXX-66334",
        payees=[("JOB BOARD ADS", 168.24, "Business Services-Advertising Services")],
    )
    accs = analyze_upload("activity (2).xlsx", raw)
    assert len(accs) == 1
    a = accs[0]
    assert a["kind"] == "credit"
    assert a["suggested_entity_type"] == "business"
    assert "Blue Business Cash" in (a.get("human_title") or a.get("suggested_nickname") or "")
    assert a.get("last4") == "6334"


def test_chase_filename_last4_and_credit_kind():
    assert last4_from_text("Chase4411_Activity_20260115.csv") == "4411"
    assert last4_from_text("Chase5522_Activity_20260115.csv") == "5522"
    kind = guess_account_kind(
        filename="Chase4411_Activity_20260115.csv",
        headers=["Card", "Transaction Date", "Post Date", "Description", "Category", "Type", "Amount"],
        raw_text="Card,Transaction Date,Post Date,Description,Category,Type,Amount\n4411,08/05/2026,08/06/2026,OFFICE SUPPLY CO,Office & Shipping,Sale,-120.00",
    )
    assert kind == "credit"
    disc = guess_account_kind(
        filename="Discover-AllAvailable-20260115.csv",
        headers=["Trans. Date", "Post Date", "Description", "Amount", "Category"],
        raw_text="DIRECTPAY FULL BALANCE,Restaurants",
    )
    assert disc == "credit"


def test_home_depot_pdf_last4():
    blob = "HOME DEPOT CREDIT SERVICES\nThis Account is Issued by Citibank, N.A.\nAccount number ending in 3333\nCredit Limit $5,000.00"
    assert last4_from_text(blob) == "3333"
    assert guess_account_kind(filename="statement.pdf", raw_text=blob) == "credit"


def test_empty_csv_is_skipped():
    accs = analyze_upload("Transactions-2026-07-16.csv", b"")
    assert accs[0].get("skip") is True
    assert accs[0]["transactions_found"] == 0


def test_plan_always_offers_personal_and_business(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    csv = (
        b"Card,Transaction Date,Post Date,Description,Category,Type,Amount\n"
        b"4411,08/05/2026,08/06/2026,OFFICE SUPPLY CO,Office & Shipping,Sale,-120.00\n"
        b"4411,07/28/2026,07/28/2026,SOFTWARE SUB,Office & Shipping,Sale,-64.56\n"
    )
    with Session() as session:
        seed_all(session)
        plan = build_smart_plan(session, [("Chase4411_Activity_20260115.csv", csv)])
        types = {e["entity_type"] for e in plan["entities"]}
        assert "personal" in types
        assert "business" in types
        acc = plan["sources"][0]["accounts"][0]
        assert acc["kind"] == "credit"
        assert acc.get("last4") == "4411"
        assert acc["suggested_entity_type"] == "business"
