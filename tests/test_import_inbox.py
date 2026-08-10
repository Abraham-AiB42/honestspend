"""Bank guides + inbox CSV import (freeware money-in)."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Profile, init_db
from financial_os.seed import seed_all
from financial_os.services.bank_guides import get_bank_guide, list_bank_guides
from financial_os.services.import_inbox import (
    ensure_inbox_layout,
    process_inbox,
    resolve_account_for_file,
)
from financial_os.services.onboarding import apply_first_run


def _session(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 'financial_os.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_bank_guides_list():
    body = list_bank_guides()
    assert "guides" in body
    assert len(body["guides"]) >= 5
    assert get_bank_guide("chase") is not None
    assert get_bank_guide("nope") is None


def test_inbox_import_matches_account(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    apply_first_run(
        s,
        cash_name="Primary checking",
        cash_balance=Decimal("1000"),
        card_name="Everyday card",
        card_balance=Decimal("100"),
        card_limit=Decimal("5000"),
    )
    s.commit()

    layout = ensure_inbox_layout()
    inbox = Path(layout["inbox"])
    csv = inbox / "Everyday-card-export.csv"
    csv.write_text(
        "Date,Description,Amount\n"
        "2026-08-01,COFFEE SHOP,-4.50\n"
        "2026-08-02,MARKET,-22.00\n",
        encoding="utf-8",
    )

    acct, score, mode = resolve_account_for_file(s, csv)
    assert acct is not None
    assert score > 0
    assert mode == "filename"
    assert "card" in (acct.nickname or "").lower() or acct.kind == "credit"

    result = process_inbox(s, dry_run=False)
    s.commit()
    assert result["files_seen"] == 1
    assert result["transactions_created"] >= 1
    assert result.get("next_steps")
    # file archived
    assert not csv.exists()
    s.close()


def test_inbox_empty(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    ensure_inbox_layout()
    result = process_inbox(s)
    assert result["files_seen"] == 0
    s.close()


def test_inbox_refuses_unmatched_filename(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    apply_first_run(
        s,
        cash_name="Primary checking",
        cash_balance=Decimal("1000"),
    )
    s.commit()
    layout = ensure_inbox_layout()
    path = Path(layout["inbox"]) / "mystery-export.csv"
    path.write_text(
        "Date,Description,Amount\n2026-08-01,X,-1.00\n",
        encoding="utf-8",
    )
    result = process_inbox(s)
    assert result["files_seen"] == 1
    assert result["transactions_created"] == 0
    assert result["results"][0]["ok"] is False
    assert "match" in (result["results"][0].get("error") or "").lower()
    assert path.exists()  # not archived on failure
    s.close()


def test_inbox_default_only_when_passed(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("1000"))
    s.commit()
    from financial_os.db import Account

    acct = s.query(Account).filter(Account.kind == "checking").one()
    layout = ensure_inbox_layout()
    path = Path(layout["inbox"]) / "mystery-export.csv"
    path.write_text(
        "Date,Description,Amount\n2026-08-01,X,-1.00\n",
        encoding="utf-8",
    )
    # Explicit default allows unmatched
    result = process_inbox(s, default_account_id=acct.id)
    assert result["transactions_created"] >= 1
    assert result["results"][0].get("match_mode") == "default"
    s.close()


def test_inbox_set_books_next_steps(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("500"))
    s.commit()
    layout = ensure_inbox_layout()
    path = Path(layout["inbox"]) / "Primary-checking.csv"
    path.write_text(
        "Date,Description,Amount,Balance\n"
        "2026-08-01,COFFEE,-4.50,995.50\n",
        encoding="utf-8",
    )
    result = process_inbox(s)
    assert result["transactions_created"] >= 1
    actions = {st.get("action") for st in result.get("next_steps") or []}
    assert "set_books_from_bank" in actions
    s.close()
