"""Freeze statement cycles with actual balances."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Profile, init_db
from financial_os.seed import seed_all
from financial_os.services.statement_freeze import freeze_statement_cycle, list_statement_cycles


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


def test_freeze_records_actual_and_variance(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("800"),
        credit_limit=Decimal("5000"),
        statement_close_day=18,
        payment_due_day=15,
        autopay_policy="statement",
    )
    s.add(card)
    s.flush()
    s.commit()

    as_of = date(2026, 9, 20)  # after close day 18
    r = freeze_statement_cycle(
        s,
        card.id,
        actual_balance=Decimal("750"),
        cycle_end=date(2026, 9, 18),
        source="import",
        as_of=as_of,
    )
    s.commit()
    assert r["actual_balance"] == "750.00"
    assert r["cycle_end"] == "2026-09-18"
    assert r["status"] == "closed"
    assert r["source"] == "import"
    # projected from books at close ~800
    assert r["projected_balance"] is not None
    assert r["variance"] == "-50.00"

    items = list_statement_cycles(s, card.id)
    assert len(items) == 1
    assert items[0]["actual_balance"] == "750.00"
