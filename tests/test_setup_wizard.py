"""Smart setup wizard state machine — 2-min primary + optional power depth."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, AppSettings, Profile, init_db
from financial_os.seed import seed_all
from financial_os.services import setup_wizard as sw
from financial_os.services.onboarding import apply_first_run, apply_quick_setup, get_onboarding_status


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


def test_advance_welcome_to_storage(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "storage"
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "security"
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "path"
    s.close()


def _to_path(s):
    """welcome → storage → security → path"""
    sw.advance_setup(s, action="next")
    sw.advance_setup(s, action="next")
    sw.advance_setup(s, action="next")


def test_choose_csv_path_short_to_power_menu(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _to_path(s)
    st = sw.advance_setup(s, action="set_path", path="csv")
    assert st["path"] == "csv"
    assert st["phase"] == "cash_loop"
    ids = [x["id"] for x in st["steps"]]
    assert ids == [
        "welcome",
        "storage",
        "security",
        "path",
        "cash_loop",
        "power_menu",
        "done",
    ]
    assert "discover" not in ids  # depth is optional, not forced tunnel
    assert st["needs_setup"] is True
    s.close()


def test_choose_plaid_then_power_menu(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _to_path(s)
    st = sw.advance_setup(s, action="set_path", path="plaid")
    assert st["phase"] == "plaid_keys"
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "plaid_link"
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "power_menu"
    assert st["plaid_item_limit"] == 10
    assert "power_modules" in st
    assert any(m["id"] == "ai_keys" for m in st["power_modules"])
    s.close()


def test_manual_path_and_complete(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _to_path(s)
    st = sw.advance_setup(s, action="set_path", path="manual")
    assert st["phase"] == "manual"
    ids = [x["id"] for x in st["steps"]]
    assert "power_menu" in ids
    assert "storage" in ids
    assert "security" in ids
    # Cannot complete without cash
    try:
        sw.mark_setup_done(s, note="test")
        raised = False
    except ValueError:
        raised = True
    assert raised
    st = sw.mark_setup_done(s, note="test", force_empty=True)
    assert st["phase"] == "done"
    assert st["needs_setup"] is False
    assert st["onboarding_complete"] is True
    row = s.get(AppSettings, 1)
    assert row.onboarding_complete is True
    s.close()


def test_power_menu_jump_and_return(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _to_path(s)
    sw.advance_setup(s, action="set_path", path="csv")
    # Seed cash so we can finish later
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    s.add(
        Account(
            profile_id=p.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("1000"),
            is_cash_for_ifpp=True,
        )
    )
    s.commit()
    st = sw.advance_setup(s, action="next")  # cash_loop → power_menu
    assert st["phase"] == "power_menu"
    st = sw.advance_setup(s, action="jump", target_phase="categorize")
    assert st["phase"] == "categorize"
    assert st["in_power_depth"] is True
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "power_menu"
    st = sw.advance_setup(s, action="jump", target_phase="discover")
    assert st["phase"] == "discover"
    st = sw.advance_setup(s, action="back")
    assert st["phase"] == "power_menu"
    # Finish from power_menu
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "done"
    assert st["needs_setup"] is False
    s.close()


def test_first_run_without_complete_lands_power_menu(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    res = apply_first_run(
        s,
        cash_name="Main",
        cash_balance=Decimal("500"),
        complete_setup=False,
    )
    s.commit()
    assert res["onboarding_complete"] is False
    assert res["setup_phase"] == "power_menu"
    st = sw.get_setup_state(s)
    assert st["phase"] == "power_menu"
    assert st["needs_setup"] is True
    assert st["can_complete"] is True
    s.close()


def test_back_navigation(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _to_path(s)
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
    sw.advance_setup(s, action="next", payload={"seen_welcome": True})  # → storage
    sw.advance_setup(s, action="next")  # → security
    sw.advance_setup(s, action="next")  # → path
    st = sw.advance_setup(
        s, action="set_path", path="csv", payload={"checking_draft": 1}
    )
    assert st["payload"].get("seen_welcome") is True
    assert st["payload"].get("checking_draft") == 1
    s.close()


def test_csv_path_has_no_liabilities_phase(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _to_path(s)
    st = sw.advance_setup(s, action="set_path", path="csv")
    ids = [x["id"] for x in st["steps"]]
    assert "liabilities" not in ids
    s.close()


def test_complete_with_cash(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    s.add(
        Account(
            profile_id=p.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("1000"),
            is_cash_for_ifpp=True,
        )
    )
    s.commit()
    st = sw.mark_setup_done(s)
    assert st["phase"] == "done"
    assert st["can_complete"] is True
    s.close()
