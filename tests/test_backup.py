"""Local backup create / list / prune / auto schedule."""

from pathlib import Path

from honestspend.config import settings
from honestspend.db import AppSettings, init_db, make_engine, make_session_factory
from honestspend.services.backup import (
    apply_pending_restore,
    create_backup,
    db_status,
    list_backups,
    maybe_auto_backup,
    prune_backups,
    restore_from_backup,
    restore_pending,
    schedule_status,
)


def test_create_list_restore_backup(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)

    db = data / "honestspend.db"
    data.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"SQLite format 3\x00" + b"A" * 200)

    st = db_status()
    assert st["db_exists"] is True
    assert st["db_size_bytes"] > 0

    res = create_backup(as_zip=True, note="test")
    assert res["ok"]
    assert res["name"].endswith(".zip")
    assert Path(res["path"]).is_file()

    names = [b.name for b in list_backups()]
    assert res["name"] in names

    original = db.read_bytes()
    db.write_bytes(b"CORRUPTED_LIVE")
    out = restore_from_backup(res["name"])
    assert out["ok"]
    assert out.get("requires_restart") is True
    assert out.get("staged") is True
    # Live DB not replaced until apply_pending_restore
    assert db.read_bytes() == b"CORRUPTED_LIVE"
    assert restore_pending() is True
    applied = apply_pending_restore()
    assert applied and applied["ok"]
    assert db.read_bytes().startswith(b"SQLite format 3")
    assert original == db.read_bytes()
    assert restore_pending() is False


def test_prune_and_auto_backup(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)

    # Real SQLite via SQLAlchemy, then bulk-backup/prune file copies
    eng = make_engine()
    init_db(eng)
    Session = make_session_factory(eng)
    with Session() as s:
        if not s.get(AppSettings, 1):
            s.add(
                AppSettings(
                    id=1,
                    auto_backup_enabled=True,
                    auto_backup_interval_hours=24,
                    auto_backup_keep=3,
                )
            )
            s.commit()

    for _ in range(5):
        create_backup(as_zip=True, note="bulk")
    pruned = prune_backups(keep=2)
    assert pruned["removed_count"] >= 3
    assert len(list_backups()) <= 2

    with Session() as s:
        st = schedule_status(s)
        assert st["enabled"] is True
        assert st["due_now"] is True
        ran = maybe_auto_backup(s, force=True)
        s.commit()
        assert ran and ran["ok"]
        assert ran["backup"]["name"].endswith(".zip")

