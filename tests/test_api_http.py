"""HTTP contract tests (FastAPI TestClient)."""

from pathlib import Path

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


def test_health(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_ifpp_entity_scope(client: TestClient):
    r = client.get("/api/ifpp", params={"scope": "entity"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ifpp_scope") == "entity"
    assert "cash_spendable" in body


def test_ifpp_group_scope(client: TestClient):
    r = client.get("/api/ifpp", params={"scope": "group"})
    assert r.status_code == 200
    assert r.json().get("ifpp_scope") == "group"


def test_patch_settings_partial(client: TestClient):
    r0 = client.get("/api/settings")
    assert r0.status_code == 200
    old_mode = r0.json().get("ifpp_mode")
    r = client.patch("/api/settings", json={"safety_buffer": "1234"})
    assert r.status_code == 200
    assert str(r.json().get("safety_buffer")) in ("1234", "1234.0", "1234.00")
    # mode should still exist
    assert r.json().get("ifpp_mode") == old_mode


def test_backup_stage_restore(client: TestClient, tmp_path):
    r = client.post("/api/backup/create", json={"as_zip": True, "note": "http-test"})
    assert r.status_code == 200
    name = r.json()["name"]
    r2 = client.post(f"/api/backup/restore/{name}")
    assert r2.status_code == 200
    body = r2.json()
    assert body.get("requires_restart") is True
    assert body.get("staged") is True


def test_require_api_key_blocks(tmp_path, monkeypatch):
    data = tmp_path / "data2"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "require_api_key", True)

    import financial_os.api.app as app_mod

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)

    with TestClient(app_mod.app) as c:
        r = c.get("/api/settings")
        assert r.status_code == 401
        r2 = c.get("/api/health")
        assert r2.status_code == 200


def test_reconcile_endpoint(client: TestClient):
    r = client.get("/api/reconcile")
    assert r.status_code == 200
    assert "accounts" in r.json()


def test_archive_account(client: TestClient):
    profiles = client.get("/api/profiles").json()
    pid = profiles[0]["id"]
    created = client.post(
        "/api/accounts",
        json={
            "profile_id": pid,
            "kind": "checking",
            "nickname": "ToArchive",
            "current_balance": "10",
            "is_cash_for_ifpp": True,
        },
    )
    assert created.status_code == 200
    aid = created.json()["id"]
    r = client.post(f"/api/accounts/{aid}/archive")
    assert r.status_code == 200
    assert r.json().get("archived") is True
    # still listed unless we filter — archive endpoint returns ok
    r2 = client.post(f"/api/accounts/{aid}/unarchive")
    assert r2.status_code == 200
