"""Live-books import brief (dream H1-A1)."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from honestspend.config import settings
from honestspend.db import Account, Transaction, init_db, make_engine, make_session_factory
from honestspend.seed import seed_all
from honestspend.services.import_brief import build_import_brief, build_post_import_next_steps


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "require_api_key", False)
    monkeypatch.setattr(settings, "allow_non_loopback", False)

    import honestspend.api.app as app_mod

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as s:
        seed_all(s)
        s.commit()

    with TestClient(app_mod.app) as c:
        yield c


def test_import_brief_uncategorized(client: TestClient, tmp_path, monkeypatch):
    data = tmp_path / "d2"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    eng = make_engine()
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        from honestspend.db import Profile

        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        acct = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("3000"),
            is_cash_for_ifpp=True,
        )
        s.add(acct)
        s.flush()
        s.add(
            Transaction(
                profile_id=personal.id,
                account_id=acct.id,
                txn_date=date.today() - timedelta(days=1),
                amount=Decimal("-42.50"),
                payee="COFFEE SHOP",
                category_id=None,
                status="cleared",
            )
        )
        s.flush()
        brief = build_import_brief(s, profile_id=personal.id)
        assert brief["uncategorized_count"] == 1
        assert brief["attention"] == "action"
        assert brief["primary_action"] == "review"
        assert "COFFEE" in (brief["sample_uncategorized"][0] if brief["sample_uncategorized"] else "")


def test_import_brief_books_vs_bank_drift(tmp_path: Path):
    from honestspend.db import Profile
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    eng = create_engine(f"sqlite:///{(tmp_path / 'd.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    try:
        seed_all(s)
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        acct = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Primary",
            current_balance=Decimal("100"),
            institution_balance=Decimal("500"),
            is_cash_for_ifpp=True,
        )
        s.add(acct)
        s.flush()
        brief = build_import_brief(s, profile_id=personal.id)
        assert brief["attention"] == "action"
        assert brief["primary_action"] == "set_books_from_bank"
        assert brief["drift_count"] == 1
        assert brief["account_id"] == acct.id
    finally:
        s.close()
        eng.dispose()


def test_import_brief_honesty_beats_uncategorized(tmp_path: Path):
    """Safe-to-spend honesty ranks above Sort charges when both apply."""
    from datetime import date
    from honestspend.db import Profile, Transaction
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    eng = create_engine(f"sqlite:///{(tmp_path / 'd2.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    try:
        seed_all(s)
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        acct = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Primary",
            current_balance=Decimal("100"),
            institution_balance=Decimal("500"),
            is_cash_for_ifpp=True,
        )
        s.add(acct)
        s.flush()
        s.add(
            Transaction(
                profile_id=personal.id,
                account_id=acct.id,
                txn_date=date.today(),
                amount=Decimal("-9.99"),
                payee="UNCATEGORIZED",
                category_id=None,
                status="cleared",
            )
        )
        s.flush()
        brief = build_import_brief(s, profile_id=personal.id)
        assert brief["primary_action"] == "set_books_from_bank"
        assert brief["secondary_action"] == "review"
        assert brief["uncategorized_count"] == 1
    finally:
        s.close()
        eng.dispose()


def test_post_import_next_steps_uncategorized(tmp_path: Path):
    from honestspend.db import Profile
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine

    eng = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    try:
        seed_all(s)
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        acct = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("1000"),
            is_cash_for_ifpp=True,
        )
        s.add(acct)
        s.flush()
        s.add(
            Transaction(
                profile_id=personal.id,
                account_id=acct.id,
                txn_date=date.today(),
                amount=Decimal("-10"),
                payee="UNCATEGORIZED SHOP",
                category_id=None,
                status="cleared",
            )
        )
        s.flush()
        steps = build_post_import_next_steps(
            s, profile_id=personal.id, created=1, categorized=0, source="CSV"
        )
        assert steps
        assert steps[0]["action"] == "review"
        assert "Sort" in steps[0]["label"]
    finally:
        s.close()
        eng.dispose()


def test_post_import_enter_ending_bal_when_no_institution(tmp_path: Path):
    """Bal-less CSV/PDF: prompt to type ending bal so Safe to spend stays honest."""
    from honestspend.db import Profile
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    eng = create_engine(f"sqlite:///{(tmp_path / 'bal.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    try:
        seed_all(s)
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        acct = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("1000"),
            is_cash_for_ifpp=True,
        )
        s.add(acct)
        s.flush()
        steps = build_post_import_next_steps(
            s,
            profile_id=personal.id,
            account_id=acct.id,
            created=3,
            categorized=0,
            institution_balance=None,
            source="PDF",
        )
        assert steps
        assert steps[0]["action"] == "enter_ending_bal"
        assert steps[0]["account_id"] == str(acct.id)
        assert "ending" in steps[0]["label"].lower() or "balance" in steps[0]["label"].lower()

        # Skip-only reimport (all duplicates) still prompts when no institution bal
        steps2 = build_post_import_next_steps(
            s,
            profile_id=personal.id,
            account_id=acct.id,
            created=0,
            skipped_existing=3,
            categorized=0,
            institution_balance=None,
            source="CSV",
        )
        assert steps2
        assert steps2[0]["action"] == "enter_ending_bal"
    finally:
        s.close()
        eng.dispose()


def test_home_simple_includes_books_brief(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 5000})
    r = client.get("/api/home/simple")
    assert r.status_code == 200
    body = r.json()
    assert "books_brief" in body
    assert body["books_brief"].get("attention") in (
        "action",
        "watch",
        "clear",
        "optional",
    )
