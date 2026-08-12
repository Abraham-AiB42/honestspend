"""Budget reserve uses max remaining per category, not sum of periods."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Category, Profile, init_db
from honestspend.seed import seed_all
from honestspend.services.budget_service import budget_reserve_total, create_rule, budgets_status


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


def test_reserve_max_per_category_not_stack(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cat = (
        s.query(Category)
        .filter(Category.profile_id == p.id)
        .order_by(Category.id)
        .first()
    )
    assert cat is not None
    create_rule(
        s,
        profile_id=p.id,
        category_id=cat.id,
        period="daily",
        amount=Decimal("50"),
    )
    create_rule(
        s,
        profile_id=p.id,
        category_id=cat.id,
        period="monthly",
        amount=Decimal("800"),
    )
    s.commit()
    st = budgets_status(s, profile_id=p.id, as_of=date.today())
    assert "max_remaining_per_category" in str(st.get("reserve_mode") or "")
    # With no spend, remaining ≈ plan; must not be 50+800
    reserve = budget_reserve_total(s, profile_id=p.id, as_of=date.today())
    assert reserve <= Decimal("800.01")
    assert reserve >= Decimal("49")  # at least daily if monthly inactive weirdness
    # stacking would be ~850
    assert reserve < Decimal("850")
    s.close()
