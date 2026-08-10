"""Month-close + promo brief (dream H1-B / H1-C3)."""

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


def test_month_close_shape(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 8000})
    r = client.get("/api/home/month-close")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("title") == "Close the month"
    assert body.get("total") >= 5
    assert len(body.get("steps") or []) >= 5
    ids = [s["id"] for s in body["steps"]]
    assert "fees" in ids
    assert "sort_charges" in ids
    assert "promo" in ids


def test_home_simple_has_month_close_and_promo(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={
            "cash_balance": 8000,
            "card_name": "0pct",
            "card_limit": 5000,
            "card_due_day": 15,
        },
    )
    r = client.get("/api/home/simple")
    body = r.json()
    assert "month_close" in body
    assert "promo_brief" in body
    assert body["month_close"].get("progress_label")


def test_mark_month_closed(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 8000})
    r = client.get("/api/home/month-close")
    body = r.json()
    assert "closed_this_period" in body
    assert body.get("closed_this_period") is False
    recon = next(s for s in body["steps"] if s["id"] == "reconcile")
    # No bank bal yet — does not block (money-in habit not started)
    assert recon.get("done") is True

    # Force mark even if optional steps remain (test path)
    r2 = client.post("/api/home/month-close/complete", json={"force": True})
    assert r2.status_code == 200, r2.text
    done = r2.json()
    assert done.get("ok") is True
    assert done.get("period")
    assert done.get("checklist", {}).get("closed_this_period") is True

    r3 = client.get("/api/home/month-close")
    assert r3.json().get("closed_this_period") is True
    assert r3.json().get("can_mark_closed") is False
