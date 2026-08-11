"""Smart setup wizard state machine (PR1)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import AppSettings, init_db
from financial_os.seed import seed_all
from financial_os.services import setup_wizard as sw
from financial_os.services.onboarding import apply_quick_setup, get_onboarding_status


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


def test_fresh_setup_needs_wizard(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    st = sw.get_setup_state(s)
    assert st["phase"] == "welcome"
    assert st["needs_setup"] is True
    assert st["path"] is None
    assert st["progress_pct"] == 0
    s.close()


def test_advance_welcome_to_path(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "path"
    s.close()


def test_choose_csv_path(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    sw.advance_setup(s, action="next")  # welcome -> path
    st = sw.advance_setup(s, action="set_path", path="csv")
    assert st["path"] == "csv"
    assert st["phase"] == "cash_loop"
    assert st["needs_setup"] is True
    s.close()


def test_choose_plaid_then_ai_sequence(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    sw.advance_setup(s, action="next")
    st = sw.advance_setup(s, action="set_path", path="plaid")
    assert st["phase"] == "plaid_keys"
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "plaid_link"
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "ai_keys"
    assert st["plaid_item_limit"] == 10
    s.close()


def test_manual_path_and_complete(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    sw.advance_setup(s, action="next")
    st = sw.advance_setup(s, action="set_path", path="manual")
    assert st["phase"] == "manual"
    st = sw.mark_setup_done(s, note="test")
    assert st["phase"] == "done"
    assert st["needs_setup"] is False
    assert st["onboarding_complete"] is True
    row = s.get(AppSettings, 1)
    assert row.onboarding_complete is True
    s.close()


def test_back_navigation(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    sw.advance_setup(s, action="next")
    sw.advance_setup(s, action="set_path", path="csv")
    st = sw.advance_setup(s, action="back")
    assert st["phase"] == "path"
    s.close()


def test_quick_setup_marks_wizard_done(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    apply_quick_setup(s, cash_name="Ops", cash_balance=Decimal("1000"))
    s.commit()
    st = sw.get_setup_state(s)
    assert st["phase"] == "done"
    assert st["needs_setup"] is False
    assert get_onboarding_status(s).complete is True
    s.close()


def test_payload_merge(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    sw.advance_setup(s, action="next", payload={"seen_welcome": True})
    st = sw.advance_setup(
        s, action="set_path", path="csv", payload={"checking_draft": 1}
    )
    assert st["payload"].get("seen_welcome") is True
    assert st["payload"].get("checking_draft") == 1
    s.close()
