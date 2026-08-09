"""Tax year checklist + named scenarios (dream H2)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from financial_os.config import settings
from financial_os.db import init_db, make_engine, make_session_factory
from financial_os.seed import seed_all


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "require_api_key", False)
    import financial_os.api.app as app_mod

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as s:
        seed_all(s)
        s.commit()
    with TestClient(app_mod.app) as c:
        yield c


def test_tax_year_checklist(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 5000})
    r = client.get("/api/tax/year-checklist")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "steps" in body
    assert body.get("year")
    assert "progress_label" in body


def test_scenarios_crud(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 8000})
    r = client.post(
        "/api/scenarios/quick",
        json={"name": "Big TV", "amount": 900},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario"]["name"] == "Big TV"
    assert "simulation" in body
    sid = body["scenario"]["id"]
    lst = client.get("/api/scenarios").json()
    assert lst["count"] >= 1
    run = client.post(f"/api/scenarios/{sid}/run")
    assert run.status_code == 200
    assert "simulation" in run.json()


def test_home_has_tax_year(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 4000})
    h = client.get("/api/home/simple").json()
    assert "tax_year" in h
    assert h["tax_year"].get("steps")


def test_debt_report(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={
            "cash_balance": 5000,
            "card_name": "Card",
            "card_balance": 400,
            "card_limit": 3000,
            "card_due_day": 15,
        },
    )
    r = client.get("/api/reports/debt")
    assert r.status_code == 200, r.text
    assert "debts" in r.json()
