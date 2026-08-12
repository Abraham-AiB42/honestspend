"""Autopay, money map, fiscal brief offline."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from honestspend.config import settings
from honestspend.db import Account, Transaction, init_db, make_engine, make_session_factory
from honestspend.seed import seed_all
from honestspend.services.profiles import create_profile
from honestspend.services.intermix import apply_intermix


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


def test_autopay_statement(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={
            "cash_balance": 2000,
            "card_name": "Card",
            "card_limit": 5000,
            "card_balance": 400,
            "card_due_day": 15,
        },
    )
    cards = [a for a in client.get("/api/accounts").json() if a["kind"] == "credit"]
    assert cards
    cid = cards[0]["id"]
    r = client.put(f"/api/autopay/{cid}", json={"policy": "statement", "apply_schedule": True})
    assert r.status_code == 200, r.text
    assert r.json()["policy"] == "statement"
    assert Decimal(r.json()["suggested_amount"]) == Decimal("400.00")
    lst = client.get("/api/autopay").json()
    assert lst["count"] >= 1


def test_digest_brief_offline(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 3000})
    r = client.get("/api/digest/brief", params={"use_grok": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] in ("offline", "offline_fallback")
    assert body["brief"]
    assert "Fiscal" in body["brief"] or "alert" in body["brief"].lower() or "clear" in body["brief"].lower()


def test_intermix_graph(tmp_path: Path):
    eng = make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(__import__("honestspend.db", fromlist=["Profile"]).Profile).filter_by(
            slug="personal"
        ).one()
        biz = create_profile(s, display_name="Biz", entity_type="business")
        p = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="P",
            current_balance=Decimal("1000"),
            is_cash_for_ifpp=True,
        )
        b = Account(
            profile_id=biz.id,
            kind="checking",
            nickname="B",
            current_balance=Decimal("100"),
            is_cash_for_ifpp=True,
        )
        s.add_all([p, b])
        s.flush()
        apply_intermix(
            s,
            kind="capital_inject",
            amount=Decimal("50"),
            from_account_id=p.id,
            to_account_id=b.id,
        )
        s.commit()
        from honestspend.services.intermix_graph import build_money_map

        g = build_money_map(s, days=30)
        assert g["count"] >= 1
        assert g["edges"][0]["from_profile_id"] == personal.id
