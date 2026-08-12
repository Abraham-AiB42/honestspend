"""Store embed must find engine/web/plaid-link.html, not Lib/web."""

from __future__ import annotations

from pathlib import Path

from honestspend.web_paths import resolve_web_dir


def test_resolve_web_dir_from_site_packages_layout(tmp_path: Path):
    api = tmp_path / "python" / "Lib" / "site-packages" / "honestspend" / "api"
    api.mkdir(parents=True)
    (api / "app.py").write_text("# stub\n", encoding="utf-8")
    web = tmp_path / "web"
    web.mkdir()
    (web / "plaid-link.html").write_text("<html></html>", encoding="utf-8")
    found = resolve_web_dir(start=api / "app.py")
    assert found == web


def test_resolve_web_dir_from_repo_src_layout(tmp_path: Path):
    api = tmp_path / "src" / "honestspend" / "api"
    api.mkdir(parents=True)
    (api / "app.py").write_text("# stub\n", encoding="utf-8")
    web = tmp_path / "web"
    web.mkdir()
    (web / "plaid-link.html").write_text("<html></html>", encoding="utf-8")
    found = resolve_web_dir(start=api / "app.py")
    assert found == web


def test_resolve_web_dir_missing_returns_none(tmp_path: Path):
    start = tmp_path / "nowhere" / "app.py"
    start.parent.mkdir()
    start.write_text("", encoding="utf-8")
    assert resolve_web_dir(start=start) is None
