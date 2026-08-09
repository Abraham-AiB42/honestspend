"""Path jail + upload safety."""

from pathlib import Path

import pytest

from financial_os.config import settings
from financial_os.services.paths_safe import (
    enforce_upload_size,
    resolve_under_data_dir,
    safe_filename,
)


def test_safe_filename_strips_traversal():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("C:\\\\Windows\\\\foo.csv") == "foo.csv"
    assert safe_filename(None, default="x.bin") == "x.bin"


def test_resolve_under_data_dir(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    inside = data / "inbox" / "a.csv"
    inside.parent.mkdir()
    inside.write_text("x", encoding="utf-8")
    got = resolve_under_data_dir(inside)
    assert got == inside.resolve()
    with pytest.raises(ValueError):
        resolve_under_data_dir(tmp_path / "outside.txt")


def test_enforce_upload_size():
    enforce_upload_size(b"ok")
    with pytest.raises(ValueError):
        enforce_upload_size(b"x" * (51 * 1024 * 1024))
