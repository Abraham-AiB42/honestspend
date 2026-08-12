"""Store 10.1.2.10: python.exe must sit next to python3xx.dll.

Cert failed launch with: "The code execution cannot proceed because
python312.dll was not found." A python.exe without its sibling DLL — or a
PATH fallback to WindowsApps python — produces that exact loader dialog.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from honestspend.services.engine_runtime import (
    is_runnable_embed,
    resolve_embed_python,
    zip_has_runnable_embed,
)

REPO = Path(__file__).resolve().parents[1]


def test_exe_without_dll_is_not_runnable(tmp_path: Path):
    py = tmp_path / "python"
    py.mkdir()
    (py / "python.exe").write_bytes(b"fake")
    assert is_runnable_embed(tmp_path) is False
    assert resolve_embed_python(tmp_path) is None


def test_src_only_tree_is_not_runnable(tmp_path: Path):
    src = tmp_path / "src" / "honestspend"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    assert is_runnable_embed(tmp_path) is False
    assert resolve_embed_python(tmp_path) is None


def _write_embed(tmp_path: Path, *, dll: str = "python314.dll", honestspend: bool = True) -> Path:
    py = tmp_path / "python"
    py.mkdir()
    (py / "python.exe").write_bytes(b"MZ")
    (py / dll).write_bytes(b"MZ" + b"\0" * 64)
    if honestspend:
        pkg = py / "Lib" / "site-packages" / "honestspend"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
    return py


def test_python3_stub_dll_is_not_runnable(tmp_path: Path):
    """Stable-ABI stub python3.dll is not the versioned runtime (Store 10.1.2.10)."""
    _write_embed(tmp_path, dll="python3.dll")
    assert is_runnable_embed(tmp_path) is False
    assert resolve_embed_python(tmp_path) is None


def test_versioned_dll_without_honestspend_is_not_runnable(tmp_path: Path):
    _write_embed(tmp_path, dll="python314.dll", honestspend=False)
    assert is_runnable_embed(tmp_path) is False


def test_exe_plus_python314_dll_is_runnable(tmp_path: Path):
    py = _write_embed(tmp_path, dll="python314.dll")
    assert is_runnable_embed(tmp_path) is True
    assert resolve_embed_python(tmp_path) == py / "python.exe"


def test_empty_dll_does_not_count(tmp_path: Path):
    py = tmp_path / "python"
    py.mkdir()
    (py / "python.exe").write_bytes(b"MZ")
    (py / "python312.dll").write_bytes(b"")
    assert is_runnable_embed(tmp_path) is False


def test_zip_requires_sibling_dll(tmp_path: Path):
    zpath = tmp_path / "engine-portable.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("python/python.exe", b"MZ")
        zf.writestr("python/python3.dll", b"MZ" + b"\0" * 64)
    assert zip_has_runnable_embed(zpath) is False

    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("python/python.exe", b"MZ")
        zf.writestr("python/python314.dll", b"MZ" + b"\0" * 64)
    assert zip_has_runnable_embed(zpath) is True


def test_checked_in_engine_zip_includes_python312_dll():
    candidates = [
        REPO / "dist" / "engine-portable.zip",
        REPO / "clients" / "HonestSpend.WinUI" / "engine-portable.zip",
    ]
    zips = [p for p in candidates if p.is_file()]
    if not zips:
        # Fresh clone before first bundle — layout helper still covered above.
        return
    for zpath in zips:
        assert zip_has_runnable_embed(zpath), f"{zpath} missing python.exe + python3xx.dll"


def test_winui_refuses_path_python_and_requires_dll():
    """C# launch path must not Process.Start('python') and must require the DLL."""
    backend = (REPO / "clients/HonestSpend.WinUI/Services/BackendHost.cs").read_text(
        encoding="utf-8"
    )
    assert "IsRunnableEmbed" in backend
    assert "IsAllowedEngineRoot" in backend
    assert "python3[0-9]" in backend or "IsVersionedRuntimeDll" in backend
    assert 'return "python";' not in backend
    assert "PYTHONDONTWRITEBYTECODE" in backend
    assert "IsPackaged" in backend


def test_msix_script_stages_unpacked_engine_with_dll():
    """Store package must include loose engine\\python\\ + python3*.dll, not only the zip."""
    script = (REPO / "scripts/package-msix.ps1").read_text(encoding="utf-8")
    assert "python3*.dll" in script or "python314.dll" in script
    assert "import honestspend" in script
    assert "python314.dll" in script or "RequiredDll" in script
    assert "clients\\HonestSpend.WinUI\\engine" in script.replace("/", "\\") or (
        "HonestSpend.WinUI" in script and "engine" in script
    )
    csproj = (REPO / "clients/HonestSpend.WinUI/HonestSpend.WinUI.csproj").read_text(
        encoding="utf-8"
    )
    assert "engine\\" in csproj
    assert "python314.dll" in csproj
    prep = (REPO / "scripts/prepare-engine-bundle.ps1").read_text(encoding="utf-8")
    assert 'PythonVersion = "3.14.' in prep
