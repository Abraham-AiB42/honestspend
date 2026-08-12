"""CLI backup / health helpers."""

import json
from pathlib import Path

from honestspend.cli import main
from honestspend.config import settings
from honestspend.db import init_db, make_engine


def test_backup_force_cli(tmp_path, monkeypatch, capsys):
    data = tmp_path / "d"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    # ensure db exists
    eng = make_engine()
    init_db(eng)
    # minimal file if create_all empty-ish
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    if not settings.db_path.exists() or settings.db_path.stat().st_size < 100:
        # init_db should have created real sqlite
        pass

    code = main(["backup", "--force", "--note", "test"])
    out = capsys.readouterr().out
    assert code == 0
    body = json.loads(out)
    assert body.get("ok") is True
    assert body.get("name", "").endswith(".zip")


def test_health_offline(monkeypatch, capsys):
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "port", 59999)  # unlikely
    code = main(["health"])
    assert code == 1
    assert "offline" in capsys.readouterr().out.lower() or "unhealthy" in capsys.readouterr().out.lower()
