"""At-rest DB seal / unseal and key wrap."""

from __future__ import annotations

from pathlib import Path

from financial_os.config import settings
from financial_os.db import init_db, make_engine
from financial_os.services import db_crypto as dc
from financial_os.services import db_runtime as rt


def _prep(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    eng = make_engine()
    init_db(eng)
    eng.dispose()
    # touch a real sqlite file with content
    assert settings.db_path.is_file()
    assert settings.db_path.read_bytes()[:15] == b"SQLite format 3"
    return data


def test_seal_unseal_roundtrip(tmp_path: Path, monkeypatch):
    data = _prep(tmp_path, monkeypatch)
    pin = "2468"
    out = dc.enable_encryption(secret=pin, mode_hint="pin", wrap="password", seal_now=False)
    assert not out.get("dek_b64")  # password wrap must not leak DEK
    dek = dc.resolve_dek(secret=pin)
    assert dc.encryption_enabled()
    assert dc.plaintext_present()

    seal = dc.seal_database(dek)
    assert seal["ok"]
    assert not dc.plaintext_present()
    assert dc.sealed_present()
    assert dc.needs_unlock()

    un = dc.unseal_database(dek)
    assert un["ok"]
    assert dc.plaintext_present()
    assert (data / "financial_os.db").is_file()


def test_wrong_pin_fails(tmp_path: Path, monkeypatch):
    _prep(tmp_path, monkeypatch)
    dc.enable_encryption(secret="1234", mode_hint="pin", wrap="password")
    meta = dc.load_meta()
    try:
        dc.unwrap_dek("9999", meta or {})
        raised = False
    except Exception:
        raised = True
    assert raised


def test_runtime_unlock_lock(tmp_path: Path, monkeypatch):
    _prep(tmp_path, monkeypatch)
    rt.boot()
    assert rt.is_unlocked()
    en = rt.enable(secret="abcd", mode_hint="pin", wrap="password")
    # password wrap must not return raw DEK
    assert not en.get("dek_b64")
    locked = rt.lock_and_seal()
    assert locked.get("sealed")
    assert not rt.is_unlocked()
    assert dc.needs_unlock()

    # API-layer style unlock
    u = rt.unlock(secret="abcd")
    assert u.get("unlocked")
    assert rt.is_unlocked()
    assert dc.plaintext_present()


def test_no_silent_boot_when_encrypted(tmp_path: Path, monkeypatch):
    _prep(tmp_path, monkeypatch)
    rt.boot()
    rt.enable(secret="9999", mode_hint="pin", wrap="password")
    assert dc.plaintext_present()
    # Soft crash: dispose without seal (plaintext leftover on disk)
    from financial_os.services import db_runtime as rtmod

    rtmod._dispose_unlocked()
    rtmod._session_dek = None
    b = rt.boot()
    assert b.get("unlocked") is False
    assert b.get("needs_unlock") is True
    # Unlock still works from leftover plaintext
    u = rt.unlock(secret="9999")
    assert u.get("unlocked") is True


def test_disable_encryption(tmp_path: Path, monkeypatch):
    _prep(tmp_path, monkeypatch)
    rt.boot()
    rt.enable(secret="zzzz", mode_hint="password", wrap="password")
    rt.lock_and_seal()
    rt.unlock(secret="zzzz")
    out = rt.disable(secret="zzzz")
    assert out.get("enabled") is False
    assert not dc.encryption_enabled()
