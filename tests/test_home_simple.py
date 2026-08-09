"""Home Simple + wealth basics."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from financial_os.config import settings
from financial_os.db import init_db, make_engine, make_session_factory
from financial_os.seed import seed_all
from financial_os.services.profiles import create_profile
from financial_os.services.wealth_basics import build_wealth_tips


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


def test_home_simple_shape(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 8000})
    r = client.get("/api/home/simple")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "safe_to_spend" in body
    assert body["status"] in ("safe", "watch", "danger")
    assert "do_this_next" in body
    assert body["do_this_next"].get("title")
    assert "setup" in body
    assert body["setup"]["has_cash"] is True
    assert "language" in body
    ritual = body.get("three_minute_check") or {}
    assert ritual.get("title") == "3-minute check"
    assert ritual.get("total") == 5
    assert len(ritual.get("steps") or []) == 5
    assert "done_count" in ritual


def test_wealth_tips_when_safe_surplus(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 10000})
    # add child for 529 tip
    profiles = client.get("/api/profiles").json()
    personal = next(p for p in profiles if p["slug"] == "personal")
    client.post(
        "/api/profiles",
        json={
            "display_name": "Kid",
            "entity_type": "child",
            "parent_profile_id": personal["id"],
        },
    )
    r = client.get("/api/home/simple")
    body = r.json()
    actions = [t["action"] for t in body.get("wealth_tips") or []]
    assert "wealth_401k_match" in actions
    assert "wealth_ira" in actions
    assert "wealth_529" in actions
    assert "wealth_iul_edu" in actions


def test_first_run_api(client: TestClient):
    r = client.post(
        "/api/onboarding/first-run",
        json={
            "cash_name": "Main checking",
            "cash_balance": 4000,
            "safety_buffer": 1000,
            "card_name": "Card",
            "card_limit": 3000,
            "card_due_day": 15,
            "bill_name": "Rent",
            "bill_amount": 1200,
            "bill_next_date": "2026-09-01",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("cash_account") == "Main checking"
    assert body.get("bill") == "Rent"
    home = client.get("/api/home/simple").json()
    assert home["setup"]["has_cash"] is True
    assert home["setup"]["has_bill"] is True
    # North star: personal day-one is not a tax-vault nag
    next_action = (home.get("do_this_next") or {}).get("action")
    assert next_action != "fund_tax_vault"
    title = ((home.get("do_this_next") or {}).get("title") or "").lower()
    assert "tax vault" not in title


def test_wealth_suppressed_when_red(tmp_path, monkeypatch):
    data = tmp_path / "d"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    eng = make_engine()
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        tips = build_wealth_tips(
            s,
            cash_spendable=Decimal("5000"),
            is_red_now=True,
            has_critical_fiscal=False,
        )
        assert tips == []
