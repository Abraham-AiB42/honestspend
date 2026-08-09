"""HTTP + service tests for Add Business / Add Child."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from financial_os.config import settings
from financial_os.db import Category, init_db, make_engine, make_session_factory
from financial_os.seed import seed_all
from financial_os.services.ifpp_service import run_ifpp
from financial_os.services.profiles import create_profile
from financial_os.db import Account
from decimal import Decimal
from datetime import date


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


def test_post_business_profile(client: TestClient):
    r = client.post(
        "/api/profiles",
        json={"display_name": "Acme Services", "entity_type": "business", "tax_form_primary": "1120S"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Acme Services"
    assert body["entity_type"] == "business"
    assert body["slug"].startswith("acme")

    r2 = client.get("/api/profiles")
    slugs = {p["slug"] for p in r2.json()}
    assert "personal" in slugs
    assert body["slug"] in slugs


def test_post_child_profile(client: TestClient):
    profiles = client.get("/api/profiles").json()
    personal_id = next(p["id"] for p in profiles if p["slug"] == "personal")
    r = client.post(
        "/api/profiles",
        json={
            "display_name": "Sam",
            "entity_type": "child",
            "parent_profile_id": personal_id,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["entity_type"] == "child"
    assert r.json()["tax_form_primary"] == "none"
    assert r.json()["parent_profile_id"] == personal_id


def test_entity_silo_after_create(tmp_path: Path):
    eng = make_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        s.commit()
        personal = s.query(__import__("financial_os.db", fromlist=["Profile"]).Profile).filter_by(
            slug="personal"
        ).one()
        biz = create_profile(s, display_name="Biz Co", entity_type="business")
        s.add(
            Account(
                profile_id=personal.id,
                kind="checking",
                nickname="P",
                current_balance=Decimal("5000"),
                is_cash_for_ifpp=True,
            )
        )
        s.add(
            Account(
                profile_id=biz.id,
                kind="checking",
                nickname="B",
                current_balance=Decimal("90000"),
                is_cash_for_ifpp=True,
            )
        )
        s.commit()
        as_of = date(2026, 8, 8)
        r_p = run_ifpp(s, as_of=as_of, profile_id=personal.id, scope="entity")
        r_b = run_ifpp(s, as_of=as_of, profile_id=biz.id, scope="entity")
        assert r_p.cash_spendable == Decimal("4000.00")
        assert r_b.cash_spendable == Decimal("89000.00")
