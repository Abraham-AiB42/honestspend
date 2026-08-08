from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Base, Category, Profile, init_db
from financial_os.seed import load_coa, seed_all


def test_coa_json_loads():
    coa = load_coa()
    assert "profiles" in coa
    assert len(coa["profiles"]) == 3
    assert coa["business_expenses"]
    meals = next(c for c in coa["business_expenses"] if c["code"] == "BIZ_OTHER_MEALS")
    assert meals["partial_rule"] == "meals_50"
    assert meals["tax_line"] == "20"


def test_seed_profiles_and_categories(tmp_path: Path):
    db = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    seed_all(session)

    profiles = {p.slug: p for p in session.query(Profile).all()}
    assert set(profiles) == {"personal", "ap_agency", "aib42"}
    assert profiles["personal"].is_default
    assert profiles["ap_agency"].tax_form_primary == "1120S"

    cats = session.query(Category).all()
    assert len(cats) > 50
    tax_forms = {c.tax_form for c in cats}
    assert "1120S" in tax_forms
    assert "SchA" in tax_forms
    assert "none" in tax_forms

    # Meals tagged partial on both business profiles
    meals = [c for c in cats if "MEALS" in c.code]
    assert len(meals) == 2
    assert all(c.partial_rule == "meals_50" for c in meals)

    session.close()
