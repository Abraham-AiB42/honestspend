"""Local BYOK secrets store (Plaid + AI)."""

from __future__ import annotations

from pathlib import Path

import pytest

from honestspend.config import settings
from honestspend.services import secrets_store as ss


@pytest.fixture()
def isolated_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    # clear process overrides between tests
    settings.plaid_client_id = None
    settings.plaid_secret = None
    settings.plaid_env = "sandbox"
    settings.xai_api_key = None
    yield tmp_path


def test_save_plaid_hot_reloads(isolated_secrets: Path):
    st = ss.save_plaid_credentials(
        client_id="client_abc_12345678",
        secret="secret_xyz_999",
        env="development",
    )
    assert st["configured"] is True
    assert st["env"] == "development"
    assert settings.plaid_enabled is True
    assert settings.plaid_client_id == "client_abc_12345678"
    assert settings.plaid_secret == "secret_xyz_999"
    assert settings.plaid_env == "development"
    assert (isolated_secrets / "secrets.json").is_file()
    # masked never shows full secret
    assert "secret_xyz" not in (st.get("client_id_masked") or "")


def test_clear_plaid(isolated_secrets: Path):
    ss.save_plaid_credentials(client_id="c" * 20, secret="s" * 20, env="sandbox")
    ss.clear_plaid_credentials()
    assert settings.plaid_enabled is False


def test_ai_xai_key(isolated_secrets: Path):
    ss.save_ai_credentials(provider="xai", api_key="xai-test-key-12345")
    assert settings.grok_enabled is True
    assert settings.xai_api_key == "xai-test-key-12345"
    providers = ss.get_ai_providers()
    xai = next(p for p in providers["providers"] if p["id"] == "xai")
    assert xai["configured"] is True


def test_invalid_env(isolated_secrets: Path):
    with pytest.raises(ValueError):
        ss.save_plaid_credentials(client_id="c", secret="s", env="live")


def test_plaid_item_limit_constant():
    assert ss.PLAID_ITEM_TRIAL_LIMIT == 10
