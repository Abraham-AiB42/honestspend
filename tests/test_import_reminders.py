"""Freeware import reminder cadence."""

from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import AppSettings, init_db
from honestspend.seed import seed_all
from honestspend.services.import_reminders import (
    build_import_reminder,
    mark_import_activity,
    snooze_import_reminder,
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


def test_first_run_stores_cadence(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    apply_first_run(
        s,
        cash_name="Primary checking",
        cash_balance=1000,
        import_reminder_cadence="monthly",
        import_reminder_focus="statements",
    )
    s.commit()
    row = s.get(AppSettings, 1)
    assert row.import_reminder_cadence == "monthly"
    assert row.import_reminder_focus == "statements"
    s.close()


def test_off_never_due(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    row = s.get(AppSettings, 1)
    row.import_reminder_cadence = "off"
    s.commit()
    rem = build_import_reminder(s, as_of=date(2026, 8, 9))
    assert rem["due"] is False
    assert rem["cadence"] == "off"
    s.close()


def test_weekly_due_when_never_imported(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    row = s.get(AppSettings, 1)
    row.import_reminder_cadence = "weekly"
    row.import_last_at = None
    s.commit()
    rem = build_import_reminder(s, as_of=date(2026, 8, 9))
    assert rem["due"] is True
    assert rem["title"]
    s.close()


def test_mark_import_clears_due(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    row = s.get(AppSettings, 1)
    row.import_reminder_cadence = "weekly"
    s.commit()
    mark_import_activity(s, when=datetime(2026, 8, 8, 12, 0, 0))
    s.commit()
    rem = build_import_reminder(s, as_of=date(2026, 8, 9))
    assert rem["due"] is False
    # 8 days later → due
    rem2 = build_import_reminder(s, as_of=date(2026, 8, 16))
    assert rem2["due"] is True
    s.close()


def test_snooze(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    row = s.get(AppSettings, 1)
    row.import_reminder_cadence = "daily"
    row.import_last_at = None
    s.commit()
    snooze_import_reminder(s, days=7, as_of=date(2026, 8, 9))
    s.commit()
    rem = build_import_reminder(s, as_of=date(2026, 8, 10))
    assert rem["due"] is False
    rem2 = build_import_reminder(s, as_of=date(2026, 8, 17))
    assert rem2["due"] is True
    s.close()
