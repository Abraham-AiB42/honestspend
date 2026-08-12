"""Cash runway day strip."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, ScheduledItem, init_db
from honestspend.seed import seed_all
from honestspend.services.cash_runway import build_cash_runway


def _session(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_runway_flags_red_day(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    # Clear seed cash noise — use dedicated checking
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("500"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    # Kill default safety buffer for this test
    from honestspend.db import AppSettings

    st = s.get(AppSettings, 1)
    if st:
        st.safety_buffer = Decimal("0")
        st.tax_vault_enabled = False
        st.horizon_days = 30
    due = date.today() + timedelta(days=5)
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Big bill",
            amount=Decimal("-600"),
            next_date=due,
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=cash.id,
            active=True,
        )
    )
    s.commit()

    strip = build_cash_runway(s, days=30, profile_id=p.id)
    assert strip["starting_cash"] == "500.00"
    assert strip["first_red_day"] == due.isoformat()
    red_rows = [d for d in strip["days"] if d["is_red"]]
    assert red_rows
    assert red_rows[0]["date"] == due.isoformat()
    # Activity day appears in highlights
    assert any(h["date"] == due.isoformat() for h in strip["highlight_days"])
