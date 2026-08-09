"""Multi-user API keys + encrypted backup packages."""

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


def test_single_user_open_loopback(client: TestClient):
    r = client.get("/api/ifpp")
    assert r.status_code == 200
    st = client.get("/api/permissions/auth-status").json()
    assert st["multi_user_mode"] is False


def test_second_user_forces_api_key(client: TestClient):
    r = client.post(
        "/api/permissions/users",
        json={
            "username": "bookie",
            "display_name": "Books",
            "role": "bookkeeper",
            "issue_token": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["multi_user_mode"] is True
    token = body["api_token"]
    assert token

    # No key → 401
    r2 = client.get("/api/ifpp")
    assert r2.status_code == 401

    # Owner still needs a token in multi-user mode — mint via rotate owner or use bookie
    r3 = client.get("/api/ifpp", headers={"X-API-Key": token})
    assert r3.status_code == 200

    # Viewer/bookkeeper cannot create users
    r4 = client.post(
        "/api/permissions/users",
        headers={"X-API-Key": token},
        json={"username": "x", "display_name": "X", "role": "viewer"},
    )
    assert r4.status_code == 403


def test_cpa_cannot_write_settings(client: TestClient):
    r = client.post(
        "/api/permissions/users",
        json={
            "username": "cpa1",
            "display_name": "CPA",
            "role": "cpa_viewer",
            "issue_token": True,
        },
    )
    token = r.json()["api_token"]
    r2 = client.patch(
        "/api/settings",
        headers={"X-API-Key": token},
        json={"safety_buffer": "999"},
    )
    assert r2.status_code == 403


def test_encrypted_backup_roundtrip(client: TestClient, tmp_path: Path):
    pytest.importorskip("cryptography")
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 1000})
    remote = tmp_path / "cloud"
    remote.mkdir()
    client.put(
        "/api/backup/remote-config",
        json={"destination_folder": str(remote), "auto_copy_encrypted": True},
    )
    r = client.post(
        "/api/backup/create-encrypted",
        json={"password": "testpass99", "note": "unit", "copy_to_remote": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"].endswith(".lrenc")
    assert body.get("remote_path")
    assert Path(body["remote_path"]).is_file()

    # wrong password
    bad = client.post(
        "/api/backup/decrypt",
        json={"name": body["name"], "password": "wrongpass1"},
    )
    assert bad.status_code == 400

    good = client.post(
        "/api/backup/decrypt",
        json={"name": body["name"], "password": "testpass99"},
    )
    assert good.status_code == 200, good.text
    assert good.json()["name"].endswith(".zip")
