from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Base, Transaction
from financial_os.seed import seed_all
from financial_os.services.excel_import import import_budget_xlsx


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    return s


def _make_budget_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    # row1 titles (ignored mostly)
    ws.append(["2026-01-01", None, None, "Daily", None, "Income", "Daily Expences"])
    # row2 headers like real file
    ws.append(
        [
            None,
            None,
            "Daily",
            "Save",
            "AP",
            "Income",
            "Food",
            "Purch",
            "Gas",
            "Ins",
            "Home",
            "Util",
            "Net",
            "Cell",
        ]
    )
    ws.append(
        [
            datetime(2026, 1, 2),
            None,
            -50,
            None,
            None,
            2000,
            -30.5,
            -10,
            -40,
            None,
            -1100,
            None,
            None,
            -80,
        ]
    )
    ws.append([datetime(2026, 1, 3), None, 0, None, None, None, -12, None, None])
    wb.save(path)


def test_import_creates_categorized_txns(tmp_path: Path):
    s = _session(tmp_path)
    xlsx = tmp_path / "Budget.xlsx"
    _make_budget_xlsx(xlsx)
    result = import_budget_xlsx(s, xlsx, profile_slug="personal")
    assert not result.errors
    assert result.transactions_created >= 5
    txns = s.query(Transaction).all()
    assert len(txns) == result.transactions_created
    assert all(t.external_id for t in txns)
    # idempotent
    result2 = import_budget_xlsx(s, xlsx, profile_slug="personal")
    assert result2.transactions_created == 0
    assert result2.skipped_existing >= result.transactions_created
    s.close()


def test_import_since_filter(tmp_path: Path):
    s = _session(tmp_path)
    xlsx = tmp_path / "Budget.xlsx"
    _make_budget_xlsx(xlsx)
    result = import_budget_xlsx(
        s, xlsx, profile_slug="personal", since=date(2026, 1, 3)
    )
    assert result.date_from == date(2026, 1, 3)
    assert result.transactions_created >= 1
    s.close()
