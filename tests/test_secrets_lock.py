"""Secrets file exclusive locking for concurrent RMW."""

from __future__ import annotations

from pathlib import Path

from honestspend.config import settings
from honestspend.services import secrets_store as ss


def test_save_plaid_under_lock(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    st = ss.save_plaid_credentials(client_id="cid-test-12345", secret="sec-test-99999", env="sandbox")
    assert st["configured"] is True
    assert st["env"] == "sandbox"
    raw = ss.load_raw()
    assert raw["plaid"].get("client_id")
    # Round-trip decrypt
    creds = ss.get_plaid_credentials()
    assert creds["client_id"] == "cid-test-12345"
    assert creds["secret"] == "sec-test-99999"


def test_ai_runtime_credentials_order(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "xai_api_key", None)
    ss.save_ai_credentials(provider="openai", api_key="sk-test-openai")
    ss.save_ai_credentials(provider="anthropic", api_key="sk-ant-test")
    run = ss.list_ai_runtime_credentials()
    ids = [r["id"] for r in run]
    assert ids == ["openai", "anthropic"]
    assert ss.any_llm_configured() is True


def test_lock_context_manager(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    with ss.secrets_file_lock(timeout=2.0):
        # Nested writes under same process re-lock would block; use _already_locked path
        d = ss.load_raw(_already_locked=True)
        d["ai"] = {"custom": {"api_key": ss._enc_str("k")}}
        ss._write_raw(d, _already_locked=True)
    run = ss.list_ai_runtime_credentials()
    assert any(r["id"] == "custom" for r in run)
