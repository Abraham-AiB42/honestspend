"""Group-scope budget reserve sums all entities."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Category, Profile, init_db
from honestspend.seed import seed_all
from honestspend.services.budget_service import budget_reserve_total, create_rule


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


def test_group_reserve_sums_profiles(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    # Create second profile if seed only has one
    biz = s.query(Profile).filter(Profile.slug != "personal").first()
    if biz is None:
        biz = Profile(
            slug="biz",
            display_name="Biz",
            entity_type="business",
            tax_form_primary="1120S",
            is_default=False,
        )
        s.add(biz)
        s.flush()
        # minimal category for biz
        s.add(
            Category(
                profile_id=biz.id,
                code="BIZ_TEST",
                display_name="Test",
                scope="entity",
            )
        )
        s.flush()

    cat_p = (
        s.query(Category)
        .filter(Category.profile_id == personal.id)
        .order_by(Category.id)
        .first()
    )
    cat_b = (
        s.query(Category)
        .filter(Category.profile_id == biz.id)
        .order_by(Category.id)
        .first()
    )
    assert cat_p and cat_b
    create_rule(s, profile_id=personal.id, category_id=cat_p.id, period="monthly", amount=Decimal("100"))
    create_rule(s, profile_id=biz.id, category_id=cat_b.id, period="monthly", amount=Decimal("200"))
    s.commit()

    entity = budget_reserve_total(s, profile_id=personal.id, as_of=date.today(), scope="entity")
    group = budget_reserve_total(s, profile_id=None, as_of=date.today(), scope="group")
    assert entity <= Decimal("100.01")
    assert group >= entity + Decimal("50")  # includes biz envelope
    s.close()
