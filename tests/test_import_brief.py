"""Live-books import brief (dream H1-A1)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from financial_os.config import settings
from financial_os.db import Account, Transaction, init_db, make_engine, make_session_factory
from financial_os.seed import seed_all
from financial_os.services.import_brief import build_import_brief


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "require_api_key", False)
    monkeypatch.setattr(settings, "allow_non_loopback", False)

    import financial_os.api.app as app_mod

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
        from financial_os.db import Profile

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
