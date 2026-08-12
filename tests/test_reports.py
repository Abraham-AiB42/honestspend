"""Reports cashflow (dream H2-A)."""

import pytest
from fastapi.testclient import TestClient

from honestspend.config import settings
from honestspend.db import init_db, make_engine, make_session_factory
from honestspend.seed import seed_all


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "require_api_key", False)
    import honestspend.api.app as app_mod

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as s:
        seed_all(s)
        s.commit()
    with TestClient(app_mod.app) as c:
        yield c


def test_cashflow_report(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 5000})
    r = client.get("/api/reports/cashflow?days=30")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "entities" in body
    assert "totals" in body
    assert body["days"] == 30
