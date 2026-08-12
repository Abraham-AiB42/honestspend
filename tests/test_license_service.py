"""Local license activate / enforce / grace."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from honestspend.services import license_service as ls


@pytest.fixture()
def lic_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ls.settings, "data_dir", tmp_path)
    monkeypatch.setattr(ls.settings, "license_enforce", False)
    monkeypatch.setattr(ls.settings, "license_grace_days", 90)
    monkeypatch.setattr(ls.settings, "license_server_url", None)
    monkeypatch.setattr(ls.settings, "license_allow_dev_keys", False)
    # wipe any leftover
    p = tmp_path / "license.json"
    if p.exists():
        p.unlink()
    return tmp_path


def test_oss_default_unlocked(lic_dir: Path):
    st = ls.get_status()
    assert st["enforce"] is False
    assert st["licensed"] is True
    assert st["mode"] == "oss_unlocked"
    assert st["price_usd"] == 49.99
    assert st["has_local_record"] is False


def test_activate_dev_key(lic_dir: Path):
    st = ls.activate_key("HS-DEV-LIFETIME", email="test@example.com")
    assert st["activated"] is True
    assert st["has_local_record"] is True
    assert st["record_valid"] is True
    assert st["email"] == "test@example.com"
    assert (lic_dir / "license.json").is_file()
    # raw key not stored
    raw = (lic_dir / "license.json").read_text(encoding="utf-8")
    assert "HS-DEV-LIFETIME" not in raw
    assert "key_fingerprint" in raw


def test_clear_license(lic_dir: Path):
    ls.activate_key("HS-TEST-LIFETIME")
    st = ls.clear_license()
    assert st["has_local_record"] is False
    assert not (lic_dir / "license.json").exists()


def test_invalid_key(lic_dir: Path):
    with pytest.raises(ValueError):
        ls.activate_key("bad")


def test_enforce_requires_license(lic_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ls.settings, "license_enforce", True)
    st = ls.get_status()
    assert st["licensed"] is False
    assert st["gate"] == "needs_activation"

    monkeypatch.setattr(ls.settings, "license_allow_dev_keys", True)
    st = ls.activate_key("HS-DEV-LIFETIME")
    assert st["licensed"] is True
    assert st["gate"] == "ok"


def test_enforce_blocks_dev_key_without_flag(lic_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ls.settings, "license_enforce", True)
    monkeypatch.setattr(ls.settings, "license_allow_dev_keys", False)
    with pytest.raises(ValueError, match="Developer"):
        ls.activate_key("HS-DEV-LIFETIME")


def test_refresh_bumps_verified(lic_dir: Path):
    ls.activate_key("HS-TEST-LIFETIME")
    before = ls._load_raw()["last_verified_at"]
    st = ls.refresh_license()
    assert st["refreshed"] is True
    after = ls._load_raw()["last_verified_at"]
    assert after >= before


def test_lifetime_offline_past_grace(lic_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ls.settings, "license_enforce", True)
    monkeypatch.setattr(ls.settings, "license_allow_dev_keys", True)
    monkeypatch.setattr(ls.settings, "license_grace_days", 1)
    ls.activate_key("HS-DEV-LIFETIME")
    rec = ls._load_raw()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    rec["last_verified_at"] = old
    rec["activated_at"] = old
    ls._save_raw(rec)
    st = ls.get_status()
    # lifetime key/dev still valid offline past grace
    assert st["record_valid"] is True
    assert st["licensed"] is True


def test_activate_store_entitlement(lic_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ls.settings, "license_enforce", True)
    st = ls.activate_store(is_active=True, store_kind="ms_store", is_trial=False)
    assert st["store_active"] is True
    assert st["licensed"] is True
    assert st["source"] == "ms_store"
    assert st["gate"] == "ok"

    st2 = ls.activate_store(is_active=False, store_kind="ms_store")
    assert st2["store_active"] is False
    # store-sourced license cleared
    assert st2["has_local_record"] is False


def test_store_inactive_keeps_key_license(lic_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ls.settings, "license_enforce", True)
    monkeypatch.setattr(ls.settings, "license_allow_dev_keys", True)
    ls.activate_key("HS-DEV-LIFETIME")
    st = ls.activate_store(is_active=False, store_kind="ms_store")
    assert st["has_local_record"] is True
    assert st["licensed"] is True
