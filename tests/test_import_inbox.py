"""Bank guides + inbox CSV import (freeware money-in)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, ScheduledItem, init_db
from honestspend.seed import seed_all
from honestspend.services.bank_guides import get_bank_guide, list_bank_guides
from honestspend.services.import_inbox import (
    ensure_inbox_layout,
    process_inbox,
    resolve_account_for_file,
)
from honestspend.services.onboarding import apply_first_run


def _session(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 'honestspend.db').as_posix()}")
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
    from honestspend.db import Account

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


def test_inbox_weak_filename_refused(tmp_path: Path, monkeypatch):
    """Kind-only matches (e.g. 'card') must not auto-route."""
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
    path = Path(layout["inbox"]) / "export-card.csv"
    path.write_text(
        "Date,Description,Amount\n2026-08-01,X,-1.00\n",
        encoding="utf-8",
    )
    result = process_inbox(s)
    assert result["transactions_created"] == 0
    assert result["results"][0]["ok"] is False
    assert result["results"][0].get("match_mode") in ("weak", "ambiguous", "none")
    assert path.exists()
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


def test_inbox_enter_ending_bal_bal_less_csv(tmp_path: Path, monkeypatch):
    """Bal-less CSV success must surface enter_ending_bal on top-level next_steps."""
    s = _session(tmp_path, monkeypatch)
    apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("1000"))
    s.commit()
    layout = ensure_inbox_layout()
    path = Path(layout["inbox"]) / "Primary-checking.csv"
    path.write_text(
        "Date,Description,Amount\n2026-08-01,COFFEE,-4.50\n",
        encoding="utf-8",
    )
    result = process_inbox(s)
    assert result["transactions_created"] >= 1
    assert not path.exists()
    actions = {st.get("action") for st in result.get("next_steps") or []}
    assert "enter_ending_bal" in actions
    step = next(st for st in result["next_steps"] if st.get("action") == "enter_ending_bal")
    assert step.get("account_id")
    s.close()


def test_inbox_multi_account_enter_ending_bal(tmp_path: Path, monkeypatch):
    """Two bal-less cash accounts → enter_ending_bal for both."""
    from honestspend.db import Account, Profile

    s = _session(tmp_path, monkeypatch)
    apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("1000"))
    s.flush()
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    s.add(
        Account(
            profile_id=personal.id,
            kind="savings",
            nickname="Emergency savings",
            current_balance=Decimal("500"),
            is_cash_for_ifpp=True,
        )
    )
    s.commit()
    layout = ensure_inbox_layout()
    Path(layout["inbox"]).joinpath("Primary-checking.csv").write_text(
        "Date,Description,Amount\n2026-08-01,A,-1.00\n",
        encoding="utf-8",
    )
    Path(layout["inbox"]).joinpath("Emergency-savings.csv").write_text(
        "Date,Description,Amount\n2026-08-01,B,-2.00\n",
        encoding="utf-8",
    )
    result = process_inbox(s)
    enter = [st for st in (result.get("next_steps") or []) if st.get("action") == "enter_ending_bal"]
    assert len(enter) >= 2
    aids = {st.get("account_id") for st in enter}
    assert len(aids) >= 2
    s.close()


def test_inbox_archives_bal_only_ofx(tmp_path: Path, monkeypatch):
    """LEDGERBAL-only OFX (no STMTTRN) still archives and can surface set_books."""
    s = _session(tmp_path, monkeypatch)
    apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("100"))
    s.commit()
    layout = ensure_inbox_layout()
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
<ACCTID>primarychecking001
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>2500.00
<DTASOF>20260805
</LEDGERBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""
    path = Path(layout["inbox"]) / "Primary-checking.ofx"
    path.write_text(ofx, encoding="utf-8")
    result = process_inbox(s)
    entry = result["results"][0]
    assert entry.get("ok") is True
    assert entry.get("institution_balance_set") is True or entry.get("ledger_balance")
    assert not path.exists()  # archived on bal-only success
    actions = {st.get("action") for st in result.get("next_steps") or []}
    assert "set_books_from_bank" in actions
    s.close()


def test_inbox_surfaces_schedule_advance(tmp_path: Path, monkeypatch):
    """Nested CSV advance fields are copied onto entry + top-level process_inbox return."""
    s = _session(tmp_path, monkeypatch)
    apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("2000"))
    s.commit()
    from honestspend.db import Account

    cash = s.query(Account).filter(Account.kind == "checking").one()
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    rent = ScheduledItem(
        profile_id=personal.id,
        account_id=cash.id,
        name="Rent",
        amount=Decimal("-1200.00"),
        next_date=date(2026, 6, 1),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
    )
    s.add(rent)
    s.commit()

    layout = ensure_inbox_layout()
    path = Path(layout["inbox"]) / "Primary-checking.csv"
    path.write_text(
        "Date,Description,Amount\n06/01/2026,Rent ACME LLC,-1200.00\n",
        encoding="utf-8",
    )
    result = process_inbox(s, auto_categorize=False)
    s.commit()
    assert result["transactions_created"] >= 1
    assert result.get("schedule_advance_error") is None
    assert result.get("schedules_advanced", 0) >= 1
    assert "Rent" in (result.get("schedules_advanced_names") or [])
    entry = result["results"][0]
    assert entry.get("schedules_advanced", 0) >= 1
    assert "Rent" in (entry.get("schedules_advanced_names") or [])
    s.refresh(rent)
    assert rent.next_date == date(2026, 7, 1)
    s.close()
