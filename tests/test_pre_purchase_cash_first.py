"""Can I buy auto prefers cash when Safe to spend covers."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, AppSettings, Profile, init_db
from honestspend.seed import seed_all
from honestspend.services.pre_purchase import check_purchase


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


def test_auto_prefers_cash_when_both_safe(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    row = s.get(AppSettings, 1)
    row.safety_buffer = Decimal("0")
    s.add(
        Account(
            profile_id=p.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("5000"),
            is_cash_for_ifpp=True,
        )
    )
    s.add(
        Account(
            profile_id=p.id,
            kind="credit",
            nickname="Rewards",
            current_balance=Decimal("0"),
            credit_limit=Decimal("10000"),
            available_credit=Decimal("10000"),
            payment_due_day=15,
            statement_close_day=1,
            rewards_program="grocery gas dining",
        )
    )
    s.commit()
    res = check_purchase(s, amount=Decimal("100"), prefer="auto", profile_id=p.id)
    assert res["verdict"] in ("safe", "safe_via_other_method")
    assert res["recommended"]["method"] == "cash"
    assert res.get("prefer_applied") == "auto"
    s.close()
