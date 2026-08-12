"""CLI/tray refuse offline open when vault is encrypted."""

from __future__ import annotations

from pathlib import Path

import pytest

from honestspend.config import settings
from honestspend.db import init_db, make_engine
from honestspend.services import db_crypto as dc
from honestspend.services import db_runtime as rt


def test_refuse_offline_when_encrypted(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    eng = make_engine()
    init_db(eng)
    eng.dispose()
    rt.boot()
    rt.enable(secret="1234", mode_hint="pin", wrap="password")
    rt.lock_and_seal()
    with pytest.raises(RuntimeError, match="encrypted"):
        dc.refuse_offline_open_if_encrypted()
