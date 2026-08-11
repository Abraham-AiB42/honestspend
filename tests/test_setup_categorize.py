"""Setup categorize + recurring finalize."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Category, Profile, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.setup_categorize import (
    auto_apply_high_confidence,
    categorize_status,
    confirm_payee_category,
)
from financial_os.services.setup_recurring_finalize import (
    apply_recurring_choices,
    remaining_recurring,
)


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


def test_confirm_payee_and_rule(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        is_cash_for_ifpp=True,
        current_balance=Decimal("2000"),
    )
    s.add(cash)
    s.flush()
    food = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.budget_group == "food")
        .first()
    )
    if not food:
        food = s.query(Category).filter(Category.profile_id == p.id).first()
    assert food is not None
    for i in range(3):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=date.today() - timedelta(days=i * 3),
                amount=Decimal("-12.50"),
                payee="STARBUCKS STORE 123",
            )
        )
    s.commit()

    st = categorize_status(s, profile_id=p.id)
    assert st["uncategorized_count"] >= 3
    assert st["confirm_queue"]

    res = confirm_payee_category(
        s,
        payee_key="starbucks store",
        category_id=food.id,
        create_rule=True,
        profile_id=p.id,
    )
    s.commit()
    assert res["updated"] >= 3
    assert res["status"]["uncategorized_count"] == 0
    s.close()


def test_auto_apply_runs(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    # seed has rules — just ensure API path doesn't crash
    out = auto_apply_high_confidence(s, use_grok=False, min_confidence=0.85)
    assert "applied" in out
    s.close()


def test_recurring_remaining_empty_ok(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    r = remaining_recurring(s)
    assert "suggestions" in r
    out = apply_recurring_choices(s, accepted=[])
    assert out["accepted"] == 0
    s.close()
