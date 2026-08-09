from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Base, Category, Profile, init_db
from financial_os.seed import load_coa, seed_all
from financial_os.services.profiles import create_profile


def test_coa_json_loads():
    coa = load_coa()
    assert "profiles" in coa
    assert len(coa["profiles"]) == 1
    assert coa["profiles"][0]["slug"] == "personal"
    assert coa["business_expenses"]
    assert coa["child_budget"]
    meals = next(c for c in coa["business_expenses"] if c["code"] == "BIZ_OTHER_MEALS")
    assert meals["partial_rule"] == "meals_50"
    assert meals["tax_line"] == "20"


def test_seed_profiles_and_categories(tmp_path: Path):
    db = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    seed_all(session)

    profiles = {p.slug: p for p in session.query(Profile).all()}
    assert set(profiles) == {"personal"}
    assert profiles["personal"].is_default

    cats = session.query(Category).all()
    assert len(cats) > 30
    tax_forms = {c.tax_form for c in cats}
    assert "SchA" in tax_forms
    assert "none" in tax_forms
    # Business COA not until Add Business
    assert not any(c.scope == "business" for c in cats)

    session.close()


def test_create_business_and_child_coa(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)

    biz = create_profile(s, display_name="Northwind LLC", entity_type="business")
    s.commit()
    meals = [
        c
        for c in s.query(Category).filter(Category.profile_id == biz.id).all()
        if "MEALS" in c.code
    ]
    assert len(meals) == 1
    assert meals[0].partial_rule == "meals_50"
    assert meals[0].tax_form == "1120S" or meals[0].tax_form

    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    child = create_profile(
        s,
        display_name="Jordan",
        entity_type="child",
        parent_profile_id=personal.id,
    )
    s.commit()
    child_cats = s.query(Category).filter(Category.profile_id == child.id).all()
    assert len(child_cats) >= 5
    assert any("ALLOWANCE" in c.code for c in child_cats)
    s.close()
