"""Setup storage + security phases (metadata only for lock)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import init_db
from financial_os.seed import seed_all
from financial_os.services import app_security as sec
from financial_os.services import paths as pathsvc
from financial_os.services import setup_wizard as sw


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


def test_storage_choice_local(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    choice = pathsvc.apply_storage_choice(kind="local")
    assert choice["kind"] == "local"
    assert Path(choice["path"]).is_dir()


def test_storage_choice_custom(tmp_path: Path, monkeypatch):
    dest = tmp_path / "cloud" / "HonestSpend" / "data"
    monkeypatch.setattr(settings, "data_dir", tmp_path / "other")
    choice = pathsvc.apply_storage_choice(kind="custom", path=str(dest))
    assert choice["path"] == str(dest)
    assert dest.is_dir()


def test_security_validate_modes():
    assert sec.validate_security_payload(mode="none")["configured"] is False
    pin = sec.validate_security_payload(mode="pin")
    assert pin["mode"] == "pin"
    plat = sec.validate_security_payload(mode="platform")
    assert plat["platform_capability"] == "windows_hello"
    try:
        sec.validate_security_payload(mode="face")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_security_catalog_has_future_caps():
    cat = sec.security_catalog(platform="win")
    ids = {c["id"] for c in cat["capabilities"]}
    assert "windows_hello" in ids
    assert "ios_face_id" in ids
    win_hello = next(c for c in cat["capabilities"] if c["id"] == "windows_hello")
    assert win_hello["available_on_this_build"] is True
    face = next(c for c in cat["capabilities"] if c["id"] == "ios_face_id")
    assert face["available_on_this_build"] is False


def test_wizard_storage_security_flow(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    st = sw.advance_setup(s, action="next")
    assert st["phase"] == "storage"
    st = sw.advance_setup(
        s,
        action="next",
        payload={"storage_kind": "local", "storage_path": str(tmp_path / "data"), "storage_chosen": True},
    )
    assert st["phase"] == "security"
    assert st["payload"].get("storage_chosen") is True
    st = sw.advance_setup(
        s,
        action="next",
        payload={"app_lock_mode": "pin", "security_configured": True},
    )
    assert st["phase"] == "path"
    assert st["payload"].get("app_lock_mode") == "pin"
    s.close()
