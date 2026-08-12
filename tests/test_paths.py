"""Data path discovery (home + OneDrive candidates)."""

from pathlib import Path

from honestspend.config import settings
from honestspend.services.paths import data_path_info


def test_data_path_info_default(tmp_path, monkeypatch):
    data = tmp_path / "fos"
    monkeypatch.setattr(settings, "data_dir", data)
    # ensure db_path property works
    info = data_path_info()
    assert info["env_var"] == "FOS_DATA_DIR"
    assert Path(info["current"]) == data
    assert info["default"].endswith(".HonestSpend") or ".HonestSpend" in info["default"]
    assert isinstance(info["candidates"], list)
    assert any(c.get("kind") == "home" for c in info["candidates"])
    assert "hint" in info
